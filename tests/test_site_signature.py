#!/usr/bin/env python3
"""plugins/module_utils/site_signature.py (1.136.0): the signatures the log analysis and the host grouping share. The log
analysis' own signature (log_signature) is held byte for byte: its rules as they were before they moved (6ae9bd30, frozen
below) give the same signature for every line of a fixed corpus. Run: python tests/test_site_signature.py"""
import importlib.util
import os
import random
import re
import sys
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load(name, *path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, *path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SIG = load("site_signature", "plugins", "module_utils", "site_signature.py")
LF = load("log_filters_sig", "plugins", "filter", "log_filters.py")

# log_signature's rules at 6ae9bd30 (plugins/filter/log_filters.py _NORMALIZE), frozen: never edit
OLD_RULES = [
    (re.compile(r"^\s*\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\]?\s*"), ""),     # a leading ISO time
    (re.compile(r"^\s*[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+"), ""),                                  # a leading syslog time
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\b"), "<time>"),
    (re.compile(r"\b\d\d:\d\d:\d\d(\.\d+)?\b"), "<time>"),
    (re.compile(r"\b(\d{1,3}\.){3}\d{1,3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"\b([0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I), "<ip>"),
    (re.compile(r"\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\"[^\"]{0,200}\"|'[^']{0,200}'"), "<q>"),
    (re.compile(r"\[\d+\]"), "[<n>]"),
    (re.compile(r"\b\d+(\.\d+)*\b"), "<n>"),
    (re.compile(r"(<n>[\s,]*){2,}"), "<n> "),
    (re.compile(r"\s+"), " "),
]


def old_signature(message):
    s = str(message or "")
    for rx, rep in OLD_RULES:
        s = rx.sub(rep, s)
    return s.strip().lower()[:200]


LINES = [
    "2026-10-23T07:00:01Z Connection reset by 192.0.2.10 port 4711 [preauth] pid=1234",
    "Connection reset by 198.51.100.7 port 50022 [preauth] pid=99",
    'open "/srv/a b" failed: 0x1f, uuid 123e4567-e89b-12d3-a456-426614174000',
    "Oct  5 03:52:47 web01 kernel: [12345.678] EXT4-fs error (device dm-0): ext4_find_entry:1455: inode #2",
    "[2026-11-01 10:00:00.123+01:00] vpxd: Lost connection to host esx01 (fe80::1ff:fe23:4567:890a) for 30 s",
    "Failed password for invalid user admin from 203.0.113.9 port 22 ssh2",
    "MAC 00:1A:2b:3C:4d:5E link down; ifindex [7], retries 1, 2, 3",
    "deadbeefcafe1234 checksum mismatch at block 1048576 (expected 0xABCDEF)",
    "user 'svc_x' quota 95.5% over 90% on /home",
    "",
    "   ",
    "\u00e9t\u00e9 \u2014 caf\u00e9 failed at 12:00:01.5",
    "x" * 500,
    "a 1.2.3.4.5 b 198.51.100.1:8080 c 2001:db8::1 d 2001:0db8:0000:0000:0000:ff00:0042:8329",
    "Jan 01 00:00:00 host sshd[999]: pam_unix(sshd:session): session opened",
]


def corpus():
    rnd = random.Random(1801)
    parts = ["error", "WARN", "disk", "/var/log/x.log", "192.0.2.%d", "pid=%d", "[%d]", "0x%x", "'q %d'", '"z"',
             "2026-11-%02dT10:00:00Z", "%02d:%02d:%02d", "e1000e", "eth0", "a-b_c.d", "uuid 123e4567-e89b-12d3-a456-42661417400%d",
             "fe80::%x:1", "AA:BB:CC:DD:EE:%02X", "1, 2, 3", "v1.2.3", "caf\u00e9", "\t", "  "]
    out = list(LINES)
    for _ in range(2000):
        words = []
        for p in rnd.sample(parts, rnd.randint(1, 8)):
            n = p.count("%")
            words.append(p % tuple(rnd.randint(0, 59) for _ in range(n)) if n else p)
        out.append(" ".join(words))
    return out


class HeldByteForByte(unittest.TestCase):
    def test_log_signature_is_what_it_was(self):
        for line in corpus():
            self.assertEqual(LF.log_signature(line), old_signature(line), repr(line))
            self.assertEqual(SIG.signature(line), old_signature(line), repr(line))

    def test_one_copy_of_the_rules(self):
        self.assertIs(LF._NORMALIZE, LF._sig.RULES)                      # log_filters keeps no copy of its own
        self.assertEqual(os.path.realpath(LF._sig.__file__), os.path.realpath(SIG.__file__))
        self.assertEqual([r.pattern for r, _ in SIG.RULES], [r.pattern for r, _ in OLD_RULES])


class Names(unittest.TestCase):
    def test_a_hosts_names_taken_out(self):
        names = ["web01.corp.example.mil", "web01", "192.0.2.21"]
        a = SIG.signature("disk /var on web01.corp.example.mil is 91% full (web01)", names)
        self.assertEqual(a, "disk /var on <host> is <n>% full (<host>)")
        self.assertEqual(SIG.signature("web012 and myweb01 stay", ["web01"]), "web012 and myweb01 stay")
        self.assertEqual(SIG.signature("WEB01 down", ["web01"]), "<host> down")

    def test_finding_set(self):
        f1 = [{"severity": "warning", "check": "disk", "summary": "/var on web01 is 91% full"},
              {"severity": "critical", "check": "services", "summary": "sssd is down on web01"}]
        f2 = [{"severity": "critical", "check": "services", "summary": "sssd is down on web02"},
              {"severity": "warning", "check": "disk", "summary": "/var on web02 is 95% full"},
              {"severity": "warning", "check": "disk", "summary": "/var on web02 is 93% full"}]
        self.assertEqual(SIG.finding_set(f1, ["web01"]), SIG.finding_set(f2, ["web02"]))          # same findings, any order
        self.assertNotEqual(SIG.finding_set(f1, ["web01"]), SIG.finding_set(f1[:1], ["web01"]))
        self.assertNotEqual(SIG.finding_set(f1, ["web01"]), SIG.finding_set(f2, ["web03"]))        # its own name not taken out
        self.assertEqual(SIG.finding_set([], ["web01"]), "")
        self.assertEqual(SIG.finding_lines(f1, ["web01"]),
                         ["[critical] services: sssd is down on <host>", "[warning] disk: /var on <host> is <n>% full paths=[\"/var\"]"])   # 1.136.0: paths stay

    def test_group(self):
        recs = [{"host": "b", "message": "denied /usr/bin/x pid 12"}, {"host": "a", "message": "denied /usr/bin/x pid 99"},
                {"host": "a", "message": "other 1"}, {"host": "b", "message": "denied /usr/bin/x pid 7"}]
        g = SIG.group(recs, examples=2)
        self.assertEqual([(x["sig"], x["objects"], x["count"]) for x in g],
                         [("denied /usr/bin/x pid <n>", ["a", "b"], 3), ("other <n>", ["a"], 1)])
        self.assertEqual(g[0]["examples"], ["denied /usr/bin/x pid 12", "denied /usr/bin/x pid 99"])


GF = load("act_group_filters", "plugins", "filter", "act_group_filters.py")


class Groups(unittest.TestCase):
    """plugins/filter/act_group_filters.py: the hosts of a play with the same signature, the first one read."""

    def test_the_group_and_its_lead(self):
        hv = {"a": {"_site_act_sig": "s1"}, "b": {"_site_act_sig": "s2"}, "c": {"_site_act_sig": "s1"}, "d": {}, "e": {"_site_act_sig": ""}}
        hosts = ["a", "b", "c", "d", "e"]
        self.assertEqual(GF.site_act_group(hv, hosts, "c"), {"lead": "a", "same": ["a", "c"]})
        self.assertEqual(GF.site_act_group(hv, hosts, "a"), {"lead": "a", "same": ["a", "c"]})
        self.assertEqual(GF.site_act_group(hv, hosts, "b"), {"lead": "b", "same": ["b"]})
        self.assertEqual(GF.site_act_group(hv, hosts, "d"), {"lead": "d", "same": ["d"]})      # no signature: alone
        self.assertEqual(GF.site_act_group(hv, hosts, "e"), {"lead": "e", "same": ["e"]})
        self.assertEqual(GF.site_act_group(hv, ["c", "a"], "a"), {"lead": "c", "same": ["c", "a"]})   # the play's order

    def test_the_note(self):
        self.assertEqual(GF.site_act_same_note(["a"], "a"), "")
        self.assertEqual(GF.site_act_same_note(["a", "c"], "a"),
                         "The same findings on 2 hosts: a, c (ACT read a; the others take its answer).")

    def test_text_set(self):
        a = GF.site_text_set(["denied /opt/app/tool on fa01", "fapolicyd active", "denied /opt/app/tool on fa01"], ["fa01"])
        b = GF.site_text_set(["fapolicyd active", "denied /opt/app/tool on fa02"], ["fa02"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, GF.site_text_set(["fapolicyd active", "denied /opt/app/other on fa02"], ["fa02"]))
        self.assertEqual(GF.site_text_set([" ", ""]), "")


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
