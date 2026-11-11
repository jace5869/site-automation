#!/usr/bin/env python3
"""The AD health report's reader mode (1.4.0, ad_health_mode: reader / auto): what roles/ad_report/library/site_ad_reader.py
reads - the replication blobs a plain domain user may read (real bytes from a Samba AD DC: repsFrom, replUpToDateVector,
replPropertyMetaData), the DS_REPL_* XML Windows builds (msDS-NCReplInboundNeighbors, msDS-ReplValueMetaData,
msDS-ReplAttributeMetaData), an AS-REQ to a KDC and its answer, the last run's members in a file (never written in Check
mode) - and how plugins/filter/ad_filters.py judges it (ad_reader_report, ad_mode): the thresholds, what was not read
said in grey (never a guess, never a problem), the tools that need admin said once, the members since the last run.
tests/ad/run_ad_reader_test.sh runs it against two real Samba DCs.
    python3 tests/test_ad_reader.py"""
import datetime
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "ad_report", "library"))
import ad_filters as F          # noqa: E402
import site_ad_reader as M      # noqa: E402

# Read as svc_aap_adreader (a plain domain user) from a Samba 4.19 DC of corp.example.mil
REPS_FROM_DC01 = ("010000000000000011010000000000003969032103000000396903210300000000000000d0000000410000000000000000000000"
                  "00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
                  "0000000000000000000000000000000000000000000000000000000000000000d10f0000000000000000000000000000d10f0000"
                  "000000009d43779ba3ce2e45b02309a70eb708233a2e6d51c9c0cb43bdaf8193d5300b9d00000000000000000000000000000000"
                  "3d00000039623737343339642d636561332d343532652d623032332d3039613730656237303832332e5f6d736463732e636f7270"
                  "2e6578616d706c652e6d696c00")
UTD_DC01 = ("020000000000000001000000000000003a2e6d51c9c0cb43bdaf8193d5300b9dd10f00000000000000803ed5deb19d01")
PROP_META_GONE = ("010000000000000017000000000000000000000001000000796903210300000090b2334e2f302b459aba360b281692d48b100000"
                  "000000008b100000000000000300000001000000796903210300000090b2334e2f302b459aba360b281692d48b10000000000000"
                  "8b100000000000000100020001000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b100000"
                  "000000000200020001000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b10000000000000"
                  "1901020001000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b1000000000000001000900"
                  "01000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b100000000000000800090007000000"
                  "796903210300000090b2334e2f302b459aba360b281692d48e100000000000008e10000000000000100009000100000079690321"
                  "0300000090b2334e2f302b459aba360b281692d48b100000000000008b1000000000000019000900010000007969032103000000"
                  "90b2334e2f302b459aba360b281692d48b100000000000008b100000000000003700090002000000796903210300000090b2334e"
                  "2f302b459aba360b281692d48c100000000000008c100000000000004000090001000000796903210300000090b2334e2f302b45"
                  "9aba360b281692d48b100000000000008b100000000000005a00090002000000796903210300000090b2334e2f302b459aba360b"
                  "281692d48c100000000000008c100000000000005e00090002000000796903210300000090b2334e2f302b459aba360b281692d4"
                  "8c100000000000008c100000000000006000090002000000796903210300000090b2334e2f302b459aba360b281692d48c100000"
                  "000000008c100000000000006200090001000000796903210300000090b2334e2f302b459aba360b281692d48b10000000000000"
                  "8b100000000000007d00090001000000796903210300000090b2334e2f302b459aba360b281692d48c100000000000008c100000"
                  "000000009200090001000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b10000000000000"
                  "9f00090001000000796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b10000000000000a0000900"
                  "02000000796903210300000090b2334e2f302b459aba360b281692d48c100000000000008c10000000000000dd00090001000000"
                  "796903210300000090b2334e2f302b459aba360b281692d48b100000000000008b100000000000002e0109000100000079690321"
                  "0300000090b2334e2f302b459aba360b281692d48b100000000000008b1000000000000090020900010000007969032103000000"
                  "90b2334e2f302b459aba360b281692d48b100000000000008b100000000000000e03090001000000796903210300000090b2334e"
                  "2f302b459aba360b281692d48b100000000000008b10000000000000")
DC01_GUID, DC02_GUID = "d6404c69-9628-4b4c-9c7a-d4620d8bdaa3", "9b77439d-cea3-452e-b023-09a70eb70823"
DC01_INV, DC02_INV = "4e33b290-302f-452b-9aba-360b281692d4", "516d2e3a-c0c9-43cb-bdaf-8193d5300b9d"
NC = "DC=corp,DC=example,DC=mil"
NOW = "2026-11-10T09:00:00Z"


def xml(tag, **fields):
    return "<%s>%s</%s>" % (tag, "".join("<%s>%s</%s>" % (k, v, k) for k, v in fields.items()), tag)


class Blobs(unittest.TestCase):
    def test_reps_from(self):
        """DC01's repsFrom: its partner (DC02's NTDS Settings objectGUID and invocation ID), the last success and attempt, no failure."""
        r = M.parse_reps_from(bytes.fromhex(REPS_FROM_DC01))
        self.assertEqual(r["partner_guid"], DC02_GUID)
        self.assertEqual(r["invocation_id"], DC02_INV)
        self.assertEqual((r["failures"], r["result"]), (0, 0))
        self.assertEqual(r["last_success"], "2026-11-10T08:39:21Z")
        self.assertEqual(r["address"], DC02_GUID + "._msdcs.corp.example.mil")

    def test_reps_from_failing(self):
        """The same link failing: the consecutive failures and the last result as they are in the blob."""
        b = bytearray(bytes.fromhex(REPS_FROM_DC01))
        struct.pack_into("<I", b, 12, 3)
        struct.pack_into("<I", b, 32, 1722)
        r = M.parse_reps_from(bytes(b))
        self.assertEqual((r["failures"], r["result"]), (3, 1722))

    def test_utd_placeholder_is_no_date(self):
        """A cursor a new DC copied at its join carries 1970 as its time: no date, never "56 years ago"."""
        c = M.parse_utd(bytes.fromhex(UTD_DC01))
        self.assertEqual(c, [{"invocation_id": DC02_INV, "usn": 4049, "last_sync": ""}])

    def test_utd_v2_with_a_time(self):
        nt = int((datetime.datetime(2026, 11, 10, 6, 0, tzinfo=datetime.timezone.utc) - M.EPOCH_1601).total_seconds()) * 10 ** 7
        b = bytes.fromhex(UTD_DC01)[:40] + struct.pack("<q", nt)
        self.assertEqual(M.parse_utd(b)[0]["last_sync"], "2026-11-10T06:00:00Z")

    def test_prop_meta(self):
        """An account disabled on DC01: userAccountControl's version, time and the DC's invocation ID (attid 0x90008)."""
        m = M.parse_prop_meta(bytes.fromhex(PROP_META_GONE))
        self.assertEqual(m[0x90008], {"version": 7, "time": "2026-11-10T08:40:25Z", "invocation_id": DC01_INV})
        self.assertEqual(M.ATTID[0x90008], "userAccountControl")

    def test_windows_xml(self):
        n = M.parse_neighbor_xml(xml("DS_REPL_NEIGHBOR", pszNamingContext=NC, pszSourceDsaDN="CN=NTDS Settings,CN=DC02,CN=Servers,CN=Site-A,CN=Sites,CN=Configuration," + NC,
                                     ftimeLastSyncSuccess="2026-11-09T07:00:00Z", ftimeLastSyncAttempt="2026-11-10T08:00:00Z",
                                     dwLastSyncResult="1722", cNumConsecutiveSyncFailures="12"))
        self.assertEqual((n["failures"], n["result"], n["last_success"]), (12, 1722, "2026-11-09T07:00:00Z"))
        v = M.parse_value_meta_xml(xml("DS_REPL_VALUE_META_DATA", pszAttributeName="member", pszObjectDn="CN=leaver,CN=Users," + NC,
                                       ftimeDeleted="2026-11-10T07:30:00Z", ftimeCreated="2026-01-01T00:00:00Z",
                                       ftimeLastOriginatingChange="2026-11-10T07:30:00Z", dwVersion="2",
                                       pszLastOriginatingDsaDN="CN=NTDS Settings,CN=DC02,CN=Servers,CN=Site-A,CN=Sites,CN=Configuration," + NC))
        self.assertEqual((v["deleted"], v["changed"], v["version"]), ("2026-11-10T07:30:00Z", "2026-11-10T07:30:00Z", 2))
        added = M.parse_value_meta_xml(xml("DS_REPL_VALUE_META_DATA", pszObjectDn="CN=jdoe", ftimeDeleted="1601-01-01T00:00:00Z",
                                           ftimeLastOriginatingChange="2026-11-10T08:00:00Z"))
        self.assertEqual(added["deleted"], "")
        a = M.parse_attr_meta_xml(xml("DS_REPL_ATTR_META_DATA", pszAttributeName="userAccountControl", ftimeLastOriginatingChange="2026-11-10T08:10:00Z",
                                      pszLastOriginatingDsaDN="CN=NTDS Settings,CN=DC01,CN=Servers,CN=Site-A,CN=Sites,CN=Configuration," + NC))
        self.assertEqual((a["attribute"], a["time"]), ("userAccountControl", "2026-11-10T08:10:00Z"))

    def test_small_helpers(self):
        self.assertEqual(M.sid_text(M.sid_bytes("S-1-5-21-1-2-3-512")), "S-1-5-21-1-2-3-512")
        self.assertEqual(M.site_of("CN=DC01,CN=Servers,CN=Site-A,CN=Sites,CN=Configuration," + NC), "Site-A")
        self.assertIsNone(M.filetime("0"))
        self.assertIsNone(M.filetime(str(0x7FFFFFFFFFFFFFFF)))
        self.assertEqual(M.iso(M.filetime("134387424000000000")), "2026-11-10T00:00:00Z")    # lockoutTime / pwdLastSet: 100 ns since 1601
        self.assertEqual(M.dn_to_dns(NC), "corp.example.mil")


class Kerberos(unittest.TestCase):
    def krb_error(self, code, stime="20261110090000Z"):
        body = M._seq(M._ctx(0, M._int(5)), M._ctx(1, M._int(30)), M._ctx(4, M._der(0x18, stime.encode())), M._ctx(5, M._int(0)),
                      M._ctx(6, M._int(code)), M._ctx(9, M._gstr("CORP.EXAMPLE.MIL")), M._ctx(10, M._principal(2, ["krbtgt", "CORP.EXAMPLE.MIL"])))
        return M._der(0x7E, body)

    def test_as_req(self):
        """An AS-REQ for the account with no pre-authentication: APPLICATION 10, pvno 5, msg-type 10, the realm."""
        b = M.as_req("svc_aap_adreader", "CORP.EXAMPLE.MIL", nonce=1)
        self.assertEqual(b[0], 0x6A)
        self.assertIn(b"CORP.EXAMPLE.MIL", b)
        self.assertIn(b"svc_aap_adreader", b)
        self.assertIn(b"krbtgt", b)
        self.assertNotIn(b"\xa3", b[:8])                          # no padata
        self.assertIn(b"svc@other.example.mil", M.as_req("svc@other.example.mil", "CORP.EXAMPLE.MIL"))

    def test_answers(self):
        a = M.parse_krb_answer(self.krb_error(25))
        self.assertEqual((a["kind"], a["code"], a["text"], a["stime"]), ("error", 25, "pre-authentication required", "2026-11-10T09:00:00Z"))
        self.assertEqual(M.parse_krb_answer(self.krb_error(6))["text"], "the account is not known to the KDC")
        self.assertEqual(M.parse_krb_answer(b"\x6b\x03\x30\x01\x00")["kind"], "as-rep")
        with self.assertRaises(ValueError):
            M.parse_krb_answer(b"HTTP/1.1 400")

    def test_probe_a_kdc(self):
        """A KDC over TCP (a stand-in answering KRB-ERROR 25), and a port nothing listens on."""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        answer = self.krb_error(25)

        def serve():
            c, _ = srv.accept()
            n = struct.unpack(">I", c.recv(4))[0]
            got = b""
            while len(got) < n:
                got += c.recv(n - len(got))
            c.sendall(struct.pack(">I", len(answer)) + answer)
            c.close()
        t = threading.Thread(target=serve)
        t.start()
        r = M.krb_probe("127.0.0.1", port, "svc_aap_adreader", "CORP.EXAMPLE.MIL", 3)
        t.join()
        srv.close()
        self.assertTrue(r["ok"])
        self.assertEqual(r["answer"], "pre-authentication required")
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        cport = closed.getsockname()[1]
        closed.close()
        r = M.krb_probe("127.0.0.1", cport, "svc", "CORP.EXAMPLE.MIL", 1)
        self.assertFalse(r["ok"])
        self.assertIn("TCP %d" % cport, r["error"])


class History(unittest.TestCase):
    def reader(self, path, check):
        r = M.Reader({"history_file": path}, check_mode=check)
        r.domain = "corp.example.mil"
        return r

    def test_file(self):
        """No file: the first run (one line). A run writes it, a dry run (Check) never does; the next run reads it."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "site-health", "ad-privileged-groups.json")
            out = {"groups": [{"name": "Domain Admins", "member_dns": ["CN=Administrator,CN=Users," + NC]}]}
            r = self.reader(path, True)
            r.previous(out)
            r.close()
            self.assertIsNone(out["previous"])
            self.assertIn("no earlier list: the first run", out["previous_why"])
            self.assertFalse(os.path.exists(path))
            r = self.reader(path, False)
            r.previous(out)
            r.close()
            self.assertEqual(out["history_written"], path)
            with open(path) as fh:
                kept = json.load(fh)
            self.assertEqual(kept["corp.example.mil"]["groups"]["Domain Admins"], ["CN=Administrator,CN=Users," + NC])
            out2 = {"groups": []}
            r = self.reader(path, False)
            r.previous(out2)
            r.close()
            self.assertEqual(out2["previous"]["groups"]["Domain Admins"], ["CN=Administrator,CN=Users," + NC])
            self.assertEqual(out2["previous"]["source"], "the file " + path)

    def test_not_in_aap(self):
        out = {"groups": []}
        r = M.Reader({}, check_mode=False)
        r.domain = "corp.example.mil"
        r.previous(out)
        r.close()
        self.assertEqual(out["previous_why"], "no earlier list: not run from AAP (no job ID), and no ad_reader_history_file")


class LearnDomain(unittest.TestCase):
    """Short inventory names (dc01): the domain from ad_reader_domain, the names, ansible_host as an FQDN, each DC's rootDSE
    read without a bind, the EE's resolv.conf search domain - in that order; each DC then reached as its dnsHostName."""
    def learn(self, inv, domain="", rootdse=None, resolv=""):
        r = M.Reader({"domain": domain})
        calls = []

        def anon(name, address):
            calls.append(name)
            a = (rootdse or {}).get(name)
            return (a, "") if a else ({}, "[Errno 111] Connection refused")
        r.anon_rootdse = anon
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as fh:
            fh.write(resolv)
        r.RESOLV_CONF = fh.name
        try:
            inv = [dict(x) for x in inv]
            got = r.learn_domain(inv)
        finally:
            os.unlink(fh.name)
            r.close()
        return got, [d["name"] for d in inv], calls

    SD = {"dc01": {"defaultNamingContext": NC, "dnsHostName": "dc01.corp.example.mil"},
          "dc02": {"defaultNamingContext": NC, "dnsHostName": "dc02.corp.example.mil"}}

    def test_setting_first(self):
        (dom, src, _), names, _ = self.learn([{"name": "dc01", "address": ""}], domain="corp.example.mil")
        self.assertEqual((dom, src, names), ("corp.example.mil", "ad_reader_domain", ["dc01.corp.example.mil"]))

    def test_the_names(self):
        (dom, src, _), names, calls = self.learn([{"name": "dc01.corp.example.mil", "address": ""}], rootdse=self.SD)
        self.assertEqual((dom, src, calls), ("corp.example.mil", "the inventory's DC names", []))

    def test_ansible_host_fqdn(self):
        (dom, src, _), names, _ = self.learn([{"name": "dc01", "address": "DC01.corp.example.mil"}, {"name": "dc02", "address": "192.0.2.12"}])
        self.assertEqual((dom, src), ("corp.example.mil", "the inventory's ansible_host (dc01.corp.example.mil)"))
        self.assertEqual(names, ["dc01.corp.example.mil", "dc02.corp.example.mil"])

    def test_rootdse(self):
        """ansible_host an address: the rootDSE (no bind) gives the domain and each DC's own name."""
        (dom, src, _), names, calls = self.learn([{"name": "dc01", "address": "192.0.2.11"}, {"name": "dc02", "address": "2001:db8::12"}],
                                                 rootdse=self.SD, resolv="search other.example.mil\n")
        self.assertEqual((dom, src), ("corp.example.mil", "the rootDSE of dc01 (read without a bind)"))
        self.assertEqual(names, ["dc01.corp.example.mil", "dc02.corp.example.mil"])
        self.assertEqual(calls, ["dc01", "dc02"])

    def test_rootdse_of_the_second(self):
        (dom, src, _), names, _ = self.learn([{"name": "dc01", "address": ""}, {"name": "dc02", "address": ""}], rootdse={"dc02": self.SD["dc02"]})
        self.assertEqual((dom, src, names), ("corp.example.mil", "the rootDSE of dc02 (read without a bind)",
                                             ["dc01.corp.example.mil", "dc02.corp.example.mil"]))

    def test_resolv_conf_last(self):
        (dom, src, _), names, _ = self.learn([{"name": "dc01", "address": ""}], resolv="# made by NetworkManager\nsearch corp.example.mil example.mil\n")
        self.assertEqual((dom, src, names), ("corp.example.mil", "the execution environment's DNS search domain (resolv.conf)",
                                             ["dc01.corp.example.mil"]))

    def test_nothing_says_to_set_it(self):
        (dom, src, why), _, _ = self.learn([{"name": "dc01", "address": ""}])
        self.assertEqual(dom, "")
        self.assertIn("no DC answered a rootDSE read on LDAP 389 (dc01: [Errno 111] Connection refused)", why)
        self.assertTrue(why.endswith("set ad_reader_domain"))

    def test_two_domains(self):
        (dom, _, why), _, _ = self.learn([{"name": "dc01.corp.example.mil", "address": ""}, {"name": "dc01.other.example.mil", "address": ""}])
        self.assertEqual(dom, "")
        self.assertIn("more than one domain (corp.example.mil, other.example.mil)", why)

    def test_said_in_the_report(self):
        out = F.ad_reader_report(reader(domain_source="the rootDSE of dc01 (read without a bind)"), {"now": NOW})
        self.assertIn("The domain corp.example.mil: from the rootDSE of dc01 (read without a bind).", section(out, "How this was read")["text"])


def dc(name, **kw):
    fq = name.lower() + ".corp.example.mil"
    d = {"fqdn": fq, "name": name, "site": "Site-A", "os": "Windows Server 2022 Standard", "gc": True, "rodc": False, "roles": [],
         "in_ad": True, "in_inventory": True, "address": fq, "dns_a": ["192.0.2.%d" % (11 if name == "DC01" else 12)], "dns_a_error": "",
         "ldap": {"ok": True, "ms": 2, "error": ""}, "ldaps": {"ok": True, "ms": 9, "error": ""}, "bind": {"ok": True, "via": "LDAPS"},
         "synchronized": True, "gc_ready": True, "current_time": NOW, "offset": 0.0,
         "cert": {"subject": "CN=" + fq, "issuer": "CN=Issuing CA", "not_after": "2027-11-10T09:00:00Z", "trusted": True, "error": ""},
         "kerberos": {"kind": "tgt", "ok": True, "error": ""},
         "repl": {"source": "repsFrom", "why": "", "cursors": [],
                  "links": [{"nc": NC, "partner": ("dc02" if name == "DC01" else "dc01") + ".corp.example.mil", "last_success": "2026-11-10T08:45:00Z",
                             "last_attempt": "2026-11-10T08:45:00Z", "failures": 0, "result": 0}]}}
    d.update(kw)
    return d


def reader(**kw):
    r = {"ok": True, "why": "", "domain": "corp.example.mil", "forest": "corp.example.mil", "read_at": NOW, "user": "svc_aap_adreader@CORP.EXAMPLE.MIL",
         "read_from": "dc01.corp.example.mil", "read_via": "LDAPS",
         "domain_info": {"level": "7", "forest_level": "7", "schema_version": "88", "tombstone_days": "180", "recycle_bin": True,
                         "krbtgt_pwd_last_set": "2026-09-01T00:00:00Z", "rid": {"total": 1073741823, "issued": 5000}},
         "dcs": [dc("DC01", roles=["PDC emulator", "RID master", "Infrastructure master", "Schema master", "Domain naming master"]), dc("DC02")],
         "fsmo": [{"role": x, "holder": "dc01.corp.example.mil", "holder_name": "DC01", "deleted": False}
                  for x in ("PDC emulator", "RID master", "Infrastructure master", "Schema master", "Domain naming master")],
         "dns_srv": [{"record": "_ldap._tcp.dc._msdcs.corp.example.mil", "targets": ["dc01.corp.example.mil", "dc02.corp.example.mil"], "error": ""},
                     {"record": "_ldap._tcp.gc._msdcs.corp.example.mil", "targets": ["dc01.corp.example.mil", "dc02.corp.example.mil"], "error": ""}],
         "groups": [{"name": "Domain Admins", "members": 1, "member_dns": ["CN=Administrator,CN=Users," + NC], "member_names": ["Administrator"], "changes": []}],
         "group_meta": "read", "locked_out": [], "created": [], "account_changes": [], "account_changes_how": [],
         "previous": None, "previous_why": "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?)",
         "not_read": [], "errors": []}
    r.update(kw)
    return r


def judge(r, **opts):
    return F.ad_reader_report(r, dict({"now": NOW}, **opts))


def problems(out, area=None):
    return [(p["severity"], p["text"]) for p in out["problems"] if area is None or p["area"] == area]


def section(out, title):
    return next(s for s in out["report"]["sections"] if s["title"].startswith(title))


class Report(unittest.TestCase):
    def test_every_cut_list_says_so(self):
        """1.24.0: the locked-out and created lists say when they were cut at ad_changes_max, as the changed list does."""
        out = judge(reader(locked_out_capped=True, created_capped=True))
        self.assertEqual(section(out, "Accounts locked out now")["lines"],
                         ["Only the first 500 locked-out accounts were looked at (ad_changes_max)."])
        self.assertEqual(section(out, "Users, groups and computers created")["lines"],
                         ["Only the first 500 created accounts were looked at (ad_changes_max)."])
        out = judge(reader())
        self.assertNotIn("lines", section(out, "Accounts locked out now"))
        self.assertNotIn("lines", section(out, "Users, groups and computers created"))

    def test_healthy(self):
        out = judge(reader())
        self.assertEqual(out["report"]["title"], "Active Directory health report: all 2 domain controller(s) healthy (corp.example.mil)")
        self.assertEqual(out["report"]["status"], "ok")
        self.assertEqual(out["problems"], [])
        self.assertIn("reader mode", out["report"]["subtitle"])

    def test_admin_tools_said_once_in_grey(self):
        """dcdiag, repadmin, services, SYSVOL, disks, events: one grey line at the top - not a row per DC, not a problem."""
        out = judge(reader())
        first = out["report"]["sections"][0]
        self.assertEqual((first["title"], first["status"]), ("How this was read", "unknown"))
        text = json.dumps(out["report"])
        self.assertEqual(text.count("Not run - needs admin on the DCs (reader mode)"), 1)
        for word in ("dcdiag", "repadmin", "services", "SYSVOL", "disks", "event logs"):
            self.assertIn(word, first["lines"][0])
        self.assertNotIn("dcdiag", " ".join(c for s in out["report"]["sections"] for c in s.get("columns", [])))

    def test_clock(self):
        r = reader()
        r["dcs"][1]["offset"] = 150.0
        self.assertEqual(problems(judge(r), "time"), [("warning", "the clock of DC02 is +150 s off the other DCs' (Kerberos fails beyond 5 minutes)")])
        r["dcs"][1]["offset"] = -301.0
        self.assertEqual(problems(judge(r), "time")[0][0], "critical")
        self.assertEqual(problems(judge(r, clock_crit_seconds=600), "time")[0][0], "warning")

    def test_certificate(self):
        r = reader()
        r["dcs"][1]["cert"]["not_after"] = "2026-11-30T09:00:00Z"
        self.assertEqual(problems(judge(r), "certificate"), [("warning", "the LDAPS certificate of DC02 expires in 20 day(s)")])
        r["dcs"][1]["cert"]["not_after"] = "2026-11-13T09:00:00Z"
        self.assertEqual(problems(judge(r), "certificate")[0][0], "critical")
        r["dcs"][1]["cert"]["not_after"] = "2026-11-01T09:00:00Z"
        self.assertEqual(problems(judge(r), "certificate"), [("critical", "the LDAPS certificate of DC02 has EXPIRED")])
        r = reader()
        r["dcs"][0]["cert"].update(trusted=False, trust_error="unable to get local issuer certificate")
        self.assertIn("not trusted here: unable to get local issuer certificate", problems(judge(r), "certificate")[0][1])

    def test_replication(self):
        r = reader()
        r["dcs"][0]["repl"]["links"][0].update(last_success="2026-11-10T01:00:00Z")
        self.assertEqual(problems(judge(r), "replication"), [("warning", "last success 8.0 h ago")])
        r["dcs"][0]["repl"]["links"][0].update(last_success="2026-11-08T09:00:00Z", failures=40, result=1722)
        p = problems(judge(r), "replication")
        self.assertEqual(p[0][0], "critical")
        self.assertIn("40 consecutive failure(s), last success 2 days ago: error 1722 (the RPC server is unavailable", p[0][1])

    def test_replication_not_readable_is_grey(self):
        """A DC whose replication state the account may not read: a grey row, said once - never a finding, never a guess."""
        r = reader()
        for d in r["dcs"]:
            d["repl"] = {"source": "", "links": [], "cursors": [], "why": "not read (needs admin)"}
        out = judge(r)
        self.assertEqual(out["problems"], [])
        s = section(out, "Inbound replication")
        self.assertEqual(s["row_status"], ["unknown", "unknown"])
        self.assertEqual([row[5] for row in s["rows"]], ["not read (needs admin)"] * 2)
        self.assertIn("here: nothing readable", s["text"])
        self.assertIn("Grey: DC01, DC02 - the account may not read its replication state.", s["text"])
        self.assertNotIn("status", s)

    def test_cursors_when_no_links(self):
        """Only replUpToDateVector readable: when each partner's changes last arrived, judged the same way."""
        r = reader()
        r["dcs"][0]["repl"] = {"source": "replUpToDateVector", "links": [], "why": "",
                               "cursors": [{"nc": NC, "partner": "dc02.corp.example.mil", "last_sync": "2026-11-09T06:00:00Z"}]}
        self.assertEqual(problems(judge(r), "replication"), [("critical", "last success 27.0 h ago")])

    def test_dcs(self):
        r = reader()
        r["dcs"].append(dc("DC09", in_ad=False, dns_a=[], dns_a_error="no such name (NXDOMAIN)",
                           ldap={"ok": False, "error": "timed out"}, ldaps={"ok": False, "error": "timed out"}, bind={"ok": False, "error": "timed out"},
                           kerberos={"kind": "probe", "ok": None, "error": "not tried: the DC does not answer"}, cert={"error": "not tried: the DC does not answer"}))
        r["dcs"][1].update(synchronized=False)
        out = judge(r)
        p = problems(out, "dc")
        self.assertIn(("critical", "DC02 has not finished its initial replication (isSynchronized false): it does not serve logons"), p)
        self.assertIn(("warning", "dc09.corp.example.mil is in the inventory's DCs but is not a domain controller in AD (retired? take it out of the inventory)"), p)
        self.assertFalse([x for x in p if "DC09" in x[1] and x[0] == "critical"])          # not in AD: never red for its ports
        self.assertIn(("warning", "dc09.corp.example.mil has no A record: no such name (NXDOMAIN)"), problems(out, "dns"))
        self.assertEqual([d["dc"] for d in out["dcs"]], ["DC01", "DC02"])

    def test_ldaps_down_starttls_used(self):
        r = reader()
        r["dcs"][1].update(ldaps={"ok": False, "error": "[Errno 111] Connection refused"}, starttls={"ok": True}, bind={"ok": True, "via": "StartTLS"})
        self.assertEqual(problems(judge(r), "dc"), [("warning", "LDAPS 636 does not answer or bind on DC02: [Errno 111] Connection refused (StartTLS on 389 worked)")])

    def test_kerberos(self):
        r = reader()
        r["dcs"][1]["kerberos"] = {"kind": "tgt", "ok": False, "error": "Clients credentials have been revoked", "kdc_answers": True}
        self.assertEqual(problems(judge(r), "kerberos")[0][0], "warning")
        r["dcs"][1]["kerberos"] = {"kind": "probe", "ok": False, "error": "TCP 88: timed out; UDP 88: timed out"}
        self.assertEqual(problems(judge(r), "kerberos"), [("critical", "the KDC on DC02 does not answer: TCP 88: timed out; UDP 88: timed out")])
        r["dcs"][1]["kerberos"] = {"kind": "probe", "ok": True, "answer": "a ticket without pre-authentication", "no_preauth": True}
        self.assertIn("AS-REP roasting", problems(judge(r), "kerberos")[0][1])

    def test_fsmo_and_dns(self):
        r = reader()
        r["fsmo"][1] = {"role": "RID master", "holder": "", "holder_name": "DC03", "deleted": True}
        r["dns_srv"][0]["targets"] = ["dc01.corp.example.mil"]
        r["dcs"][1]["inventory_address"] = "192.0.2.99"
        out = judge(r)
        self.assertEqual(problems(out, "fsmo"), [("critical", "the RID master role is held by DC03, which is not a DC of the domain any more (seize it to a live DC)")])
        self.assertIn(("warning", "_ldap._tcp.dc._msdcs.corp.example.mil has no record for DC02 (restart Netlogon on them, or check DNS registration)"), problems(out, "dns"))
        self.assertIn(("warning", "dc02.corp.example.mil is 192.0.2.12 in DNS but 192.0.2.99 in the inventory"), problems(out, "dns"))

    def test_privileged(self):
        r = reader()
        r["groups"][0]["changes"] = [{"member": "leaver", "member_dn": "CN=leaver,CN=Users," + NC, "action": "removed", "time": "2026-11-10T07:30:00Z", "dc": "DC02"}]
        out = judge(r)
        self.assertEqual(problems(out, "privileged"), [("warning", "leaver removed from Domain Admins (on DC02)")])
        s = section(out, "Privileged group changes")
        self.assertEqual(s["rows"][0][-1], "not known (needs the event logs)")
        out = judge(r, privileged_change_severity="critical", groups_should_be_empty=["Domain Admins"])
        self.assertEqual(sorted(x[0] for x in problems(out, "privileged")), ["critical", "warning"])

    def test_since_the_last_run(self):
        r = reader(previous={"source": "job 10", "read_at": "2026-11-09T09:00:00Z",
                             "groups": {"Domain Admins": ["CN=Administrator,CN=Users," + NC, "CN=olduser,CN=Users," + NC]}})
        r["groups"][0]["member_dns"].append("CN=jdoe,CN=Users," + NC)
        out = judge(r)
        s = section(out, "Privileged groups since the last run")
        self.assertEqual(s["rows"], [["Domain Admins", "JOINED", "jdoe"], ["Domain Admins", "LEFT", "olduser"]])
        self.assertIn("job 10", s["text"])
        self.assertEqual(len(problems(out, "privileged")), 2)

    def test_no_earlier_list_is_never_a_change(self):
        out = judge(reader())
        s = section(out, "Privileged groups since the last run")
        self.assertEqual((s["status"], s["text"]), ("unknown", "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?)"))
        self.assertEqual(problems(out, "privileged"), [])

    def test_member_metadata_not_readable(self):
        out = judge(reader(group_meta="not read (needs admin)", not_read=["when members were added or removed (msDS-ReplValueMetaData of member): not read (needs admin)"]))
        self.assertIn("Not read (needs admin): the account may not read the members' replication metadata", section(out, "Privileged group changes")["text"])
        nr = section(out, "Not read")
        self.assertEqual(nr["status"], "unknown")
        self.assertEqual(out["report"]["status"], "ok")

    def test_accounts(self):
        r = reader(locked_out=[{"name": "lockme", "time": "2026-11-10T08:00:00Z"}],
                   created=[{"class": "user", "name": "newuser", "dn": "CN=newuser,OU=Staff," + NC, "time": "2026-11-10T07:00:00Z"}],
                   account_changes=[{"class": "user", "name": "gone", "change": "disabled", "time": "2026-11-10T08:40:25Z", "dc": "DC01", "how": "metadata"},
                                    {"class": "user", "name": "other", "change": "enabled", "time": "2026-11-10T08:00:00Z", "dc": "", "how": "whenChanged"}],
                   account_changes_how=["metadata", "whenChanged"])
        out = judge(r)
        self.assertEqual(section(out, "Accounts locked out now")["rows"], [["2026-11-10 08:00", "lockme"]])
        s = section(out, "Accounts enabled")
        self.assertEqual([x[3:] for x in s["rows"]], [["DISABLED", "DC01", "replication metadata"], ["ENABLED", "-", "whenChanged (best effort)"]])
        self.assertIn("Best effort", s["lines"][0])
        self.assertEqual(out["problems"], [])

    def test_not_read_at_all(self):
        """Nothing could be read: red, NOT READ and why - never green."""
        out = judge({"ok": False, "domain": "corp.example.mil", "why": "no DC of corp.example.mil could be read: dc01: the bind was refused - invalidCredentials"})
        self.assertEqual(out["report"]["title"], "Active Directory health report: NOT READ (corp.example.mil)")
        self.assertEqual((out["report"]["status"], out["critical"]), ("critical", 1))
        self.assertIn("invalidCredentials", json.dumps(out["report"]))


class FakeLdap(object):
    """ldap3's names the reader uses (ldap3 is not needed for the tests)."""
    SUBTREE, BASE = "SUBTREE", "BASE"


class FakeConn(object):
    """An ldap3 Connection whose searches answer from lists of entries, a page at a time with the paged-results cookie;
    page_size: the DC's page (the reader asks for 500)."""
    def __init__(self, by_filter, page_size=3):
        self.by_filter, self.page_size, self.calls = by_filter, page_size, []
        self.response, self.result = [], {}

    def search(self, base, flt, scope, attributes=None, controls=None, paged_size=None, paged_cookie=None):
        self.calls.append((flt, paged_cookie))
        found = []
        for key, entries in self.by_filter.items():
            if key in flt:
                found = entries
        start = int(paged_cookie or b"0")
        page = found[start:start + self.page_size] if paged_size else found
        more = paged_size and start + self.page_size < len(found)
        self.response = [{"type": "searchResEntry", "dn": e["dn"], "attributes": e["attributes"]} for e in page]
        self.result = {"result": 0, "description": "success",
                       "controls": {M.PAGED_RESULTS: {"value": {"size": 0, "cookie": str(start + self.page_size).encode() if more else b""}}}}
        return True


def accounts(n, prefix="u", **attrs):
    return [{"dn": "CN=%s%d,OU=Staff,%s" % (prefix, i, NC), "attributes": dict({"sAMAccountName": "%s%d" % (prefix, i), "objectClass": ["user"]}, **attrs)}
            for i in range(n)]


class Paging(unittest.TestCase):
    """1.24.0: every page of a search is read (the paged-results cookie followed) up to each list's cap, and each list
    says when it was cut; lockoutDuration 'until an administrator unlocks it' counts as never running out."""
    def setUp(self):
        self.had = getattr(M, "ldap3", None)
        M.ldap3 = FakeLdap

    def tearDown(self):
        if self.had is None:
            del M.ldap3
        else:
            M.ldap3 = self.had

    def read(self, conn, duration, mx=500):
        r = M.Reader({"hours": 24, "changes_max": mx})
        r.now = datetime.datetime(2026, 11, 10, 9, 0, tzinfo=datetime.timezone.utc)
        r.dsa_name = {}
        out = {"base_dn": NC, "domain_info": {"lockout_duration": duration}}
        try:
            r.read_accounts(conn, out)
        finally:
            r.close()
        return out

    @staticmethod
    def ft(t):
        return str(M.to_filetime(t))

    def test_all_pages_read(self):
        """10 created accounts, the DC gives 3 a page: all 10 read in 4 searches, not capped."""
        conn = FakeConn({"whenCreated>=": accounts(10, whenCreated="20261110080000.0Z")})
        got = M.Reader({}).search_capped(conn, NC, "(whenCreated>=x)", ["sAMAccountName"])
        self.assertEqual((len(got[0]), got[1]), (10, False))
        self.assertEqual([c[1] for c in conn.calls], [None, b"3", b"6", b"9"])
        # with a cap: read until there are more than it, then cut - and said so
        conn.calls = []
        got = M.Reader({}).search_capped(conn, NC, "(whenCreated>=x)", ["sAMAccountName"], size=4)
        self.assertEqual((len(got[0]), got[1]), (4, True))
        self.assertEqual(len(conn.calls), 2)
        self.assertEqual(M.Reader({}).search_capped(conn, NC, "(whenCreated>=x)", ["sAMAccountName"], size=10)[1], False)

    def test_every_list_says_when_it_was_cut(self):
        when = datetime.datetime(2026, 11, 10, 8, 40, tzinfo=datetime.timezone.utc)
        conn = FakeConn({"lockoutTime>=": accounts(7, "l", lockoutTime=self.ft(when)),
                         "whenCreated>=": accounts(8, "n", whenCreated="20261110080000.0Z"),
                         "whenChanged>=": accounts(9, "c", whenChanged="20261110080000.0Z", userAccountControl="514")})
        out = self.read(conn, "-18000000000", mx=5)                     # 30 minutes
        self.assertEqual((len(out["locked_out"]), len(out["created"]), len(out["account_changes"])), (5, 5, 5))
        self.assertEqual((out["locked_out_capped"], out["created_capped"], out["changes_capped"]), (True, True, True))
        out = self.read(conn, "-18000000000", mx=50)
        self.assertEqual((len(out["locked_out"]), len(out["created"]), len(out["account_changes"])), (7, 8, 9))
        self.assertEqual((out["locked_out_capped"], out["created_capped"], out["changes_capped"]), (False, False, False))

    def test_only_accounts_locked_now_are_asked_for(self):
        """A 30-minute lockout: the search asks for lockoutTime since 08:30 (FILETIME), so stale lockouts do not fill the cap."""
        conn = FakeConn({})
        self.read(conn, "-18000000000")
        flt = [c[0] for c in conn.calls if "lockoutTime" in c[0]][0]
        self.assertIn("(lockoutTime>=%s)" % self.ft(datetime.datetime(2026, 11, 10, 8, 30, tzinfo=datetime.timezone.utc)), flt)
        # one locked at 08:00 (run out) and one at 08:45 (still locked), should the DC give both
        conn = FakeConn({"lockoutTime>=": [
            {"dn": "CN=old," + NC, "attributes": {"sAMAccountName": "old", "lockoutTime": self.ft(datetime.datetime(2026, 11, 10, 8, 0, tzinfo=datetime.timezone.utc))}},
            {"dn": "CN=now," + NC, "attributes": {"sAMAccountName": "now", "lockoutTime": self.ft(datetime.datetime(2026, 11, 10, 8, 45, tzinfo=datetime.timezone.utc))}}]})
        self.assertEqual([x["name"] for x in self.read(conn, "-18000000000")["locked_out"]], ["now"])

    def test_until_an_administrator_unlocks_it(self):
        """lockoutDuration -9223372036854775808 (AD's and Samba's 'until an admin unlocks it') and 0: no crash, an
        account locked a year ago is still locked, and every account with a lockoutTime is asked for."""
        long_ago = datetime.datetime(2025, 11, 10, 9, 0, tzinfo=datetime.timezone.utc)
        for dur in ("-9223372036854775808", -9223372036854775808, -0x7FFFFFFFFFFFFFFF, "0", 0, None, ""):
            conn = FakeConn({"lockoutTime>=": [{"dn": "CN=lockme," + NC, "attributes": {"sAMAccountName": "lockme", "lockoutTime": self.ft(long_ago)}}]})
            out = self.read(conn, dur)
            self.assertEqual([x["name"] for x in out["locked_out"]], ["lockme"], dur)
            self.assertIn("(lockoutTime>=1)", [c[0] for c in conn.calls if "lockoutTime" in c[0]][0], dur)
        self.assertIsNone(M.lockout_window("-9223372036854775808"))
        self.assertEqual(M.lockout_window("-18000000000"), datetime.timedelta(minutes=30))
        self.assertEqual(M.lockout_window(datetime.timedelta(minutes=-30)), datetime.timedelta(minutes=30))


class Mode(unittest.TestCase):
    def test_mode(self):
        up = {"host": "dc01", "reachable": True, "data": {"dns": "x"}}
        down = {"host": "dc02", "reachable": False, "error": "ntlm: the specified credentials were rejected by the server", "data": None}
        self.assertEqual(F.ad_mode([up, down], "auto")["mode"], "admin")
        m = F.ad_mode([down], "auto")
        self.assertEqual(m["mode"], "reader")
        self.assertIn("no DC could be reached over WinRM with this credential (ntlm: the specified credentials were rejected", m["why"])
        self.assertEqual(F.ad_mode([], "auto")["mode"], "reader")
        self.assertEqual(F.ad_mode([up], "reader"), {"mode": "reader", "why": "ad_health_mode: reader"})
        self.assertEqual(F.ad_mode([down], "ADMIN")["mode"], "admin")

    def test_inventory_dc(self):
        self.assertEqual(F.reader_inventory_dc({"inventory_hostname": "dc01.corp.example.mil", "ansible_host": "192.0.2.11"}),
                         {"name": "dc01.corp.example.mil", "address": "192.0.2.11"})
        self.assertEqual(F.reader_inventory_dc({"inventory_hostname": "dc01", "ansible_host": "dc01"}), {"name": "dc01", "address": ""})


if __name__ == "__main__":
    unittest.main(verbosity=1)
