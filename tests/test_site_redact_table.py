"""1.77.0d (Codex): an adversarial table for plugins/module_utils/site_redact.py - Codex's table, pruned to values a
host really prints. Run: python3 -I tests/test_site_redact_table.py (it prints each failing case as input -> output).

The rule: no 4-character piece of a secret is left in what redact returns - alone, among other lines, cut after, or
cut before (a value its writer cut off). Only the markers redact writes and the name it keeps are not looked at.
The evidence lines are kept unchanged. Every case runs with each grep prefix on every line.

The values, as a host prints them:
- a quoted value, a JSON string, a directive's or a name's value may hold any of , ; : ] } ) = # a blank ' " \\
  <removed> $6$...;
- a URL's password has no blank;
- crypt hashes, keytab keys and PEM bodies have their own alphabets (crypt's ./A-Za-z0-9, hex, base64).
"""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# the module needs only the standard library
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_redact as X  # noqa: E402

PREFIXES = ("", "path:", "12:", "path:12:", "path-12-")
# Codex's secret: every character that once ended a value early, a marker, and a crypt hash inside
ANY = "vA01,vB02;vC03:vD04]vE05}vF06)vG07=vH08#vI09 vJ10'vK11\"vL12\\vM13<removed>vN14$6$salt$hash vO15"
NO_BLANK = "".join(ANY.split())
MARKS = (X.DROPPED, "grub.pbkdf2." + X.HASH_MARK, "$" + X.HASH_MARK, X.HASH_MARK, X.MARK)


def letters(alphabet, n, seed):
    """n characters of an alphabet, the same each run, no 4 of them in a row anywhere else in a case."""
    return "".join(alphabet[(i * 7 + seed * 13 + i // len(alphabet)) % len(alphabet)] for i in range(n))


CRYPT = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
HEX = "0123456789abcdef"
GRUB = "grub.pbkdf2.sha512.10000.%s.%s" % (letters(HEX, 128, 1).upper(), letters(HEX, 128, 2).upper())
HASHES = {"1": "$1$%s$%s" % (letters(CRYPT, 8, 3), letters(CRYPT, 22, 4)),
          "2b": "$2b$12$%s" % letters(CRYPT, 53, 5),
          "5": "$5$%s$%s" % (letters(CRYPT, 16, 6), letters(CRYPT, 43, 7)),
          "6": "$6$%s$%s" % (letters(CRYPT, 16, 8), letters(CRYPT, 86, 9)),
          "y": "$y$j9T$%s$%s" % (letters(CRYPT, 22, 10), letters(CRYPT, 43, 11)),
          "argon2id": "$argon2id$v=19$m=65536,t=3,p=4$%s$%s" % (letters(B64[:-2], 22, 12), letters(B64[:-2], 43, 13)),
          "pbkdf2-sha256": "$pbkdf2-sha256$31337$%s$%s" % (letters(CRYPT, 22, 14), letters(CRYPT, 43, 15))}


def dq(value):
    return json.dumps(value)


def sq(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def secret_cases():
    """(shape, the name kept, the text without a grep prefix, the secret)."""
    for name, key, text in (
            ("equals", "db_password", "db_password=" + ANY),
            ("equals spaced", "db_password", "db_password = " + ANY),
            ("equals no blank", "db_password", "export DB_PASSWORD=" + NO_BLANK),
            ("colon", "db_password", "db_password: " + ANY),
            ("powershell list", "Password", "  Password : " + ANY),
            ("single", "db_password", "db_password=" + sq(ANY)),
            ("double", "db_password", "db_password=" + dq(ANY)),
            ("json", "client_secret", json.dumps({"client_secret": ANY, "user": "u1"})),
            ("json compact", "token", json.dumps({"token": ANY, "n": 1}, separators=(",", ":"))),
            ("json in a string", "token", json.dumps({"log": json.dumps({"token": ANY})})),
            ("python dict", "password", repr({"password": ANY, "n": 1})),
            ("bindpw", "bindpw", "bindpw " + dq(ANY)),
            ("bindpw bare", "bindpw", "bindpw " + ANY),
            ("rootpw", "rootpw", "rootpw " + dq(ANY)),
            ("ldap_default_authtok", "ldap_default_authtok", "ldap_default_authtok " + dq(ANY)),
            ("ldap_default_authtok =", "ldap_default_authtok", "ldap_default_authtok = " + ANY),
            ("rocommunity", "rocommunity", "rocommunity " + dq(ANY) + " default"),
            ("rwcommunity", "rwcommunity", "rwcommunity " + dq(ANY) + " default"),
            ("com2sec", "com2sec", "com2sec readonly default " + dq(ANY)),
            ("createUser", "createUser", "createUser account SHA " + dq(ANY) + " AES " + dq(ANY)),
            ("grub password", "password", "password root " + dq(ANY)),
            ("reg query", "DefaultPassword", "    DefaultPassword    REG_SZ    " + ANY),
            ("xml", "password", "<password>" + ANY + "</password>"),
            ("url", "", "https://bob:" + NO_BLANK + "@host.example.mil/x")):
        yield name, key, text, ANY
    yield "yaml block", "password", "password: |\n  " + ANY + "\n  " + ANY + "\nnext: 1", ANY
    yield "grub password_pbkdf2", "password_pbkdf2", "password_pbkdf2 root " + GRUB, GRUB
    yield "GRUB2_PASSWORD", "GRUB2_PASSWORD", "GRUB2_PASSWORD=" + GRUB, GRUB
    for algorithm, h in sorted(HASHES.items()):
        yield "crypt " + algorithm, "", "the hash: " + h + " (from the file)", h
        yield "shadow " + algorithm, "", "account:" + h + ":19000:0:99999:7:::", h
    des = letters(CRYPT, 13, 16)
    yield "shadow DES", "", "account:" + des + ":19000:0:99999:7:::", des
    for kind in ("PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "OPENSSH PRIVATE KEY", "ENCRYPTED PRIVATE KEY"):
        body = letters(B64, 64 * 3 + 20, 17)
        lines = [body[i:i + 64] for i in range(0, len(body), 64)]
        yield "pem " + kind, "", "-----BEGIN %s-----\n%s\n-----END %s-----" % (kind, "\n".join(lines), kind), body
    key = letters(HEX, 64, 18)
    yield "keytab", "", "   2 host/server@EXAMPLE.MIL (aes256-cts-hmac-sha1-96) (0x" + key + ")", key


# what a check reads: kept as it is
EVIDENCE = ("locked:!:19000:0:99999:7:::", "locked:!!:19000:0:99999:7:::", "locked:*:19000:0:99999:7:::",
            "nopw::19000:0:99999:7:::", "root:x:0:0:root:/root:/bin/bash",
            "svc_token:x:1001:1001::/home/svc_token:/bin/bash",
            "auditctl -a always,exit -F path=/usr/bin/passwd -F key=privileged-passwd",
            "-w /etc/shadow -p wa -k identity", "passwd: files sss", "passwd:     files systemd [NOTFOUND=return] altfiles",
            "password required pam_unix.so", "password    [success=1 default=ignore] pam_unix.so sha512 shadow use_authtok",
            "PasswordAuthentication no", "HostKey /etc/ssh/ssh_host_rsa_key",
            'db_password=""', "-----BEGIN CERTIFICATE-----")


def grep(prefix, raw):
    return "\n".join(prefix + line for line in raw.split("\n"))


class RedactionTable(unittest.TestCase):
    failures = []    # each failing case as input -> output, for the run from the command line

    def assert_secret_absent(self, text, output, secret, key):
        inspected = output.replace(key, "\0", 1) if key else output
        for mark in MARKS:
            inspected = inspected.replace(mark, "\0")
        for mark in MARKS + ((key,) if key else ()):     # a cut that ends inside a marker or the name
            tail = next((k for k in range(len(mark) - 1, 0, -1) if inspected.endswith(mark[:k])), 0)
            inspected = inspected[:len(inspected) - tail]
        pieces = {secret[i:i + 4] for i in range(len(secret) - 3)}
        left = sorted(p for p in pieces if p in inspected)
        if left:
            self.failures.append((text, output))
        self.assertFalse(left, "pieces of the secret left: %r in %r" % (left, output))

    def test_secret_table(self):
        n = 0
        for shape, key, raw, secret in secret_cases():
            for prefix in PREFIXES:
                text = grep(prefix, raw)
                with self.subTest(shape=shape, prefix=prefix):
                    out = X.redact(text)
                    self.assert_secret_absent(text, out, secret, key)
                    self.assertEqual(X.redact(out), out, "twice the same")
                    n += 1
        self.assertEqual(n, 48 * len(PREFIXES))

    def test_among_other_lines(self):
        for shape, key, raw, secret in secret_cases():
            for prefix in PREFIXES:
                text = "before\n" + grep(prefix, raw) + "\nafter"
                with self.subTest(shape=shape, prefix=prefix):
                    out = X.redact(text)
                    self.assert_secret_absent(text, out, secret, key)
                    self.assertTrue(out.startswith("before\n") and out.endswith("\nafter"), out)

    def test_cut_after_redaction(self):
        for shape, key, raw, secret in secret_cases():
            for prefix in PREFIXES:
                text = grep(prefix, raw)
                out = X.redact(text)
                for limit in range(4, len(out) + 1, 7):
                    with self.subTest(shape=shape, prefix=prefix, limit=limit):
                        self.assert_secret_absent(text, out[:limit], secret, key)

    def test_a_value_its_writer_cut_off(self):
        """The text came cut (a tool that keeps the first N characters): a quote or a JSON string never closed, a PEM
        with no END line, a value cut short after its name - what is left of the value goes all the same. (A bare hash,
        keytab key or URL cut short is no longer the shape it is found by: those are cut after redaction, above.)"""
        for shape, key, raw, secret in secret_cases():
            if shape.startswith(("crypt", "shadow", "keytab", "url")):
                continue
            for prefix in PREFIXES:
                text = grep(prefix, raw)
                start = text.find(secret[:4])
                for cut in range(start + 8, len(text), 3):
                    head = text[:cut]
                    if len(secret) > 8 and secret[:8] not in head:
                        continue
                    with self.subTest(shape=shape, prefix=prefix, cut=cut):
                        self.assert_secret_absent(head, X.redact(head), secret[:max(8, cut - start)], key)

    def test_a_long_text_costs_its_length(self):
        """1.77.0d: a host's output is read up to 1 MiB (READ_MAX). Words of - or digit-dash runs, a grep prefix on every
        line, a JSON line of thousands of names: each read in a time that grows with its length, not its square
        (a quadratic pattern took 5 s on 8000 characters)."""
        import time
        cases = {"dashes": "a-" * 100000, "digit-dash": "1-" * 100000, "colons": "a:" * 100000, "grep context": "f-1-" * 50000,
                 "a long word": "a" * 200000, "names in a word": "db_password_x" * 15000, "keys in a word": "x_key-" * 35000,
                 "json names": '{"token": "a", ' * 15000, "passwd entries": "svc_token:x:1:1::/h:/b " * 9000,
                 "context lines": ("/etc/x.conf-3-" + "a-" * 40 + "\n") * 2000, "yaml": ("a:\n  - " + "b-" * 50 + "\n") * 2000,
                 "url": "a://b:" + "c" * 200000, "xml": "<password>" + "x" * 200000, "reg": "  x_password  REG_SZ " + "y-" * 100000,
                 "urls": "[" + '"http://server:8080",' * 9000 + "]", "url list": "http://a:1," * 18000,
                 "xml lines": "<password>\n" * 20000, "json next": '{"token":\n' * 20000,
                 "xml opens": "<password " * 20000, "xml pairs": "<password>a</password>" * 9000,
                 "reg padding": "x" + " " * 200000 + "REG_", "yaml padding": "a:\npassword:" + " " * 200000 + "x\n",
                 "json list under a name": '"token":\n[\n' + '"a",\n' * 40000 + "]", "url quoted list": '"http://s:8080",' * 12000,
                 "secret words": "password word " * 15000, "a dashed line": "a-" * 100000 + " password",
                 "evidence lines": "password required pam_unix.so\n" * 7000, "nested xml": "<token>" * 20000 + "x" + "</token>" * 20000,
                 "xml names": "".join("<db%d_token>a</db%d_token>" % (i, i) for i in range(6000)),
                 "nss padding": "passwd : files" + " " * 100000 + "x", "record padding": "svc_token:x:1:2:a:b:c" + " " * 100000 + ":",
                 # 1.77.0f (the review of e908ae06): every URL head re-read the rest of the run - 8.5 s at 200 KB; 1 MB now
                 "url heads (1 MB)": "x://u:p@h" + "a://" * 250000,
                 "snmp options": "snmpwalk " + "-A x " * 40000, "sudo tags": "%wheel ALL=(ALL) " + "NOPASSWD: " * 20000,
                 "sudo defaults": "Defaults " + "!rootpw, " * 20000, "chrony keys": "1 MD5 k\n" * 25000,
                 "names with digits": "wep-key0=a wep-key12=b " * 9000, "settings": "password 1234\n" * 15000,
                 # 1.77.0g (what sa-1770f noticed): camelCase names, -A/-X without an snmp word, sinks, smbpasswd
                 "camel names": "aPass=1 bPASS:2 " * 12000, "options only": "-A x " * 40000,
                 "options both": "-A x " * 40000 + "-X y", "snmpv3 options": "-a SHA -A x " * 20000,
                 "sinks": "trapsink h c\n" * 20000, "hex pairs": ("a" * 32 + ":") * 6000,
                 "smb records": "u:1:" + ("A" * 32 + ":") * 6000, "camel word": "aP" * 100000 + "ass",
                 # 1.77.0h (Codex on d5801557): three that cost their square (38 s, 23 s on 100-280 KB), and the new passes
                 "escaped quotes": "token:\n" + '\\"' * 100000, "reg columns": "Name" + "  REG_1" * 30000,
                 "nss brackets": "passwd: " + "[" * 200000 + "x", "options": "-password " * 20000,
                 "bearers": "bearer " * 28000, "snmp communities": "snmpget " + "-c x " * 40000, "gshadow": "aaaaaaaaaaaaaa:" * 13000,
                 "klist keys": "(0x" * 66000, "xml tags over lines": "<password\n" * 20000, "cdata": "<![CDATA[" * 22000,
                 "xml comments": "<!--" * 50000, "attribute tags": '<a b="password" value="x">' * 7500, "attributes": "<a " + 'k="v" ' * 33000 + ">",
                 "opened values": '"token": [' * 20000, "yaml continuations": "password: x\n" + "  y\n" * 50000,
                 "pam arguments": "auth required pam_x.so " + "a " * 100000, "comments": "PasswordAuthentication no" + " #" * 100000,
                 "iptables options": "-A x " + "-p " * 66000, "later sudo tags": "alice ALL=(root) " + "/bin/id, " * 22000 + "NOPASSWD: x",
                 # 1.77.0h (Codex, second pass): three of 1770h's own passes cost their square (378 s on 200 KB)
                 "closed xml comments": "<!--n-->" * 125000, "an attribute's long name": "<entry " + "L" * 200000 + ' value="x">',
                 "snmp path lists": "tool -c " + "/a:" * 66000}
        for name, text in cases.items():
            with self.subTest(name=name):
                t = time.time()
                X.redact(text)
                self.assertLess(time.time() - t, 3.0, "%s: %d characters" % (name, len(text)))

    def test_review_of_e908ae06(self):
        """1.77.0f: what the review of e908ae06 found (P2s), each now removed - or kept, for evidence."""
        removed = [("trapcommunity 12345", "trapcommunity <removed>"),          # a number is no exception for a secret's name
                   ("PASSWORD 12345", X.DROPPED), ("password 1234", X.DROPPED),
                   ("trapsess -v3 -u snmpuser -a SHA -A AuthPass123 -x AES -X PrivPass456 trap.example.com",
                    "trapsess -v3 -u snmpuser -a SHA -A <removed> -x AES -X <removed> trap.example.com"),
                   ("snmpwalk -v3 -l authPriv -u u -a SHA -AAuthPass123 -x AES -XPrivPass456 host",
                    "snmpwalk -v3 -l authPriv -u u -a SHA -A<removed> -x AES -X<removed> host"),
                   ("/etc/snmp/snmpd.conf:7:informsess -A AuthPass123 -X PrivPass456 h", "/etc/snmp/snmpd.conf:7:informsess -A <removed> -X <removed> h"),
                   ("1 MD5 ChronyPlainKey\n2 SHA1 HEX:00112233445566778899AABBCCDDEEFF00112233\n3 AES128 ASCII:PlainKey",
                    "1 MD5 <removed>\n2 SHA1 HEX:<removed>\n3 AES128 ASCII:<removed>"),
                   ("/etc/chrony.keys:4 PlainKeyNoType", "/etc/chrony.keys:4 <removed>"),
                   ("key HEX:00112233445566778899AABBCCDDEEFF", "key HEX:<removed>"),
                   ("wep-key0=abcdef1234567890", "wep-key0=<removed>"), ("wep-key3 = s3cr3t", "wep-key3 = <removed>"),
                   ("psk1=hunter2hunter2", "psk1=<removed>")]
        for text, want in removed:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), want)
        # snmp's pass phrases go in a command line too (DISA's text keeps its words, not a value)
        self.assertEqual(X.redact("snmpget -A AuthPass123 h", backstop=False), "snmpget -A <removed> h")
        kept = ["Defaults !rootpw", "Defaults !targetpw", "Defaults !runaspw", "Defaults rootpw, passwd_tries=3",
                "/etc/sudoers:12:Defaults !rootpw", "%wheel ALL=(ALL) NOPASSWD: ALL", "/etc/sudoers.d/wheel:%wheel ALL=(ALL) NOPASSWD: ALL",
                "alice ALL=(root) NOPASSWD:SETENV: /usr/bin/passwd", "krb5_keytab = /etc/krb5.keytab",
                "default_keytab_name = FILE:/etc/krb5.keytab", "ldap_default_authtok_type = password",
                "ldap_default_authtok_type = obfuscated_password", "offline_credentials_expiration = 1", "keyfile /etc/chrony.keys",
                "3072 SHA256:abcdefghijklmnop user@host (RSA)"]
        for text in kept:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), text)
        # a tag is a tag only in a rule; anything else of its name is a value, and a path is no value for a secret's name
        self.assertEqual(X.redact("NOPASSWD: hunter2"), "NOPASSWD: <removed>")
        self.assertEqual(X.redact("password /etc/x"), X.DROPPED)
        self.assertEqual(X.redact("ldap_default_authtok_type = hunter2"), X.DROPPED)

    def test_noticed_in_be213dad(self):
        """1.77.0g: the four shapes sa-1770f noticed in the probes - each now removed, the evidence around them kept."""
        smb_lm, smb_nt = "AAD3B435B51404EEAAD3B435B51404EE", "8846F7EAEE8FB117AD06BDD830B7586C"
        removed = [  # camelCase names: Pass after a small letter or a digit; the other stems at any boundary
                   ("authPass=Hunter2", "authPass=<removed>"), ("privPass: Hunter2", "privPass: <removed>"),
                   ("dbPASS=Hunter2", "dbPASS=<removed>"), ("authPass2=Hunter2", "authPass2=<removed>"),
                   ('{"authPass": "Hunter2", "n": 1}', '{"authPass": "<removed>", "n": 1}'),
                   ("<privPass>Hunter2</privPass>", "<privPass><removed></privPass>"),
                   ("bindPassword=Hunter2", "bindPassword=<removed>"), ("clientSecret=Hunter2", "clientSecret=<removed>"),
                   ("apiToken=Hunter2", "apiToken=<removed>"), ("authPass Hunter2", X.DROPPED),
                   # -A and -X with no snmp word: an SNMPv3 protocol or level on the line, or both options
                   ("-l authPriv -u u -A AuthPass123 -X PrivPass456 h", "-l authPriv -u u -A <removed> -X <removed> h"),
                   ("-l authNoPriv -A AuthPass123 h", "-l authNoPriv -A <removed> h"),
                   ("-a SHA-512 -A x1 -x AES256C -X x2", "-a SHA-512 -A <removed> -x AES256C -X <removed>"),
                   ("-aMD5 -AAuthPass123", "-aMD5 -A<removed>"), ("-A AuthPass123 -X PrivPass456", "-A <removed> -X <removed>"),
                   # unsure: a -u name, -v 3, or a line of -A / -X only (a command's next line) - the backstop drops it
                   ("   -A AuthPass123", X.DROPPED), ("   -A AuthPass123 \\", X.DROPPED),
                   ("-u admin -A AuthPass123 h", X.DROPPED), ("-v 3 -A AuthPass123 h", X.DROPPED),
                   # snmpd.conf's sinks: HOST COMMUNITY [PORT]
                   ("trapsink 192.0.2.1 public 162", "trapsink 192.0.2.1 <removed> 162"),
                   ("trap2sink 192.0.2.1 public", "trap2sink 192.0.2.1 <removed>"),
                   ("/etc/snmp/snmpd.conf:12:informsink h c", "/etc/snmp/snmpd.conf:12:informsink h <removed>"),
                   # smbpasswd and pdbedit -Lw: both hashes; a disabled one (X, NO PASSWORD) stays
                   ("alice:1000:%s:%s:[U          ]:LCT-5A8E0A59:" % (smb_lm, smb_nt),
                    "alice:1000:<removed>:<removed>:[U          ]:LCT-5A8E0A59:"),
                   ("bob:1001:NO PASSWORDXXXXXXXXXXXXXXXXXXXXX:%s:[U          ]:LCT-5A8E0A59:" % smb_nt,
                    "bob:1001:NO PASSWORDXXXXXXXXXXXXXXXXXXXXX:<removed>:[U          ]:LCT-5A8E0A59:"),
                   ("carl:1002:%s:%s:[U          ]:LCT-5A8E0A59:" % ("X" * 32, smb_nt),
                    "carl:1002:%s:<removed>:[U          ]:LCT-5A8E0A59:" % ("X" * 32)),
                   ("hashes %s:%s" % (smb_lm.lower(), smb_nt), "hashes <removed>:<removed>")]
        for text, want in removed:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), want)
                self.assertEqual(X.redact(want), want)              # redacting twice is the same
        # a command line keeps an unsure -A (DISA's text), and masks a sure one
        self.assertEqual(X.redact("-u admin -A AuthPass123 h", backstop=False), "-u admin -A AuthPass123 h")
        self.assertEqual(X.redact("-l authPriv -A AuthPass123 h", backstop=False), "-l authPriv -A <removed> h")
        kept = ["bypass=yes", "compass=north", "Passenger=x", "authKey=on", "trapsink h",
                "dan:1003:%s:%s:[DU         ]:LCT-5A8E0A59:" % ("X" * 32, "X" * 32),
                "md5 d41d8cd98f00b204e9800998ecf8427e", "-A INPUT -s 192.0.2.0/24",
                # iptables rules (STIG evidence), an snmp word in a comment too; audit rules with -a / -A
                "-A INPUT -p udp -m udp --dport 161 -m comment --comment snmpd -j ACCEPT",
                "-A always,exit -F arch=b64 -S adjtimex -k time-change", "-a always,exit -F arch=b64 -S execve -k exec"]
        for text in kept:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), text)

    def test_codex_review_of_d5801557(self):
        """1.77.0h: what Codex found in the release commit (gpt-6.2-astra, medium), each now removed - or kept, for evidence."""
        removed = [  # quoted SNMP values whole; a v1/v2c community; a sink's quoted community
                   ('snmpwalk -A "alpha beta" router', "snmpwalk -A <removed> router"),
                   ("snmpget -v2c -c BlueCanary router sysName.0", "snmpget -v2c -c <removed> router sysName.0"),
                   ('trapsink h "pub lic" 162', "trapsink h <removed> 162"),
                   # a value that opens [ or { on its name's line, a YAML scalar over lines, an XML element after "name:"
                   ('{"token": ["first",\n"BlueCanary"]}', '{"token": <removed>\n"<removed>"'),
                   ('"token": {"a": 1,\n "b": "BlueCanary"}\nok: 1', '"token": <removed>\n <removed>\nok: 1'),
                   ("password: first\n  BlueCanary\nother: 1", "password: <removed>\n  <removed>\nother: 1"),
                   ("password:\n<token>\nBlueCanary\n</token>", "password:\n<removed>\n<removed>\n</token>"),
                   # XML: a tag over lines, CDATA, attributes
                   ('<password\n type="text">BlueCanary\nSecondPart</password>\nafter',
                    X.DROPPED + '\n type="<removed>"><removed>\n<removed></password>\nafter'),
                   ("<token><![CDATA[</token>\nBlueCanary\n]]></token>\nafter", "<token><removed>\n<removed>\n<removed></token>\nafter"),
                   ('<password value="BlueCanary"/>', '<password value="<removed>"/>'),
                   ('<entry key="password" value="BlueCanary"/>', X.DROPPED), ('<add key="DbPassword" value="BlueCanary" />', X.DROPPED),
                   # what the backstop let pass: an [ ] that is no NSS action, a secret's name with a yes/no, a PAM argument
                   ("passwd: [BlueCanary]", "passwd: <removed>"), ("passwd: files [BlueCanary]", "passwd: <removed>"),
                   ("my_password no", X.DROPPED),
                   ("auth required pam_exec.so /usr/local/bin/check --password BlueCanary",
                    "auth required pam_exec.so /usr/local/bin/check --password <removed>"),
                   # options and names with a blank before the value
                   ("tool --db-pass BlueCanary", "tool --db-pass <removed>"), ("cmd --password 'a b' x", "cmd --password '<removed>' x"),
                   ("api-key BlueCanary", X.DROPPED), ("pw BlueCanary", X.DROPPED),
                   # HTTP authorization
                   ("Authorization: Bearer BlueCanary", "Authorization: Bearer <removed>"),
                   ("Authorization: Basic Qmx1ZUNhbmFyeQ==", "Authorization: Basic <removed>"), ("Bearer abc.def.ghi", "Bearer <removed>"),
                   # keys and hashes
                   ("2 host/node@EXAMPLE (0x0123456789abcdef)", "2 host/node@EXAMPLE (0x<removed>)"),
                   ("ops:abJnggxhB/yWI:admin:alice", "ops:<hash removed>:admin:alice"),
                   ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nYWJjZGVm\n-----END PGP PRIVATE KEY BLOCK-----",
                    "-----BEGIN PGP PRIVATE KEY BLOCK----- <removed> -----END PGP PRIVATE KEY BLOCK-----"),
                   ("1 MD5 ASCII:BlueCanary # monitoring", "1 MD5 ASCII:<removed> # monitoring"),
                   ("PasswordAuthentication no # password is x", X.DROPPED)]
        for text, want in removed:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), want)
                self.assertEqual(X.redact(want), want)
                if want != X.DROPPED:             # a line the backstop drops may keep its value in a command line
                    self.assertNotIn("BlueCanary", X.redact(text, backstop=False))
        kept = ["passwd: files sss", "passwd: files [NOTFOUND=return] sss", "PasswordAuthentication no", "cache_credentials = True",
                "PermitEmptyPasswords no", "password requisite pam_pwquality.so try_first_pass local_users_only retry=3",
                "password sufficient pam_unix.so sha512 shadow use_authtok", "-password optional pam_gnome_keyring.so use_authtok",
                "password required pam_pwhistory.so use_authtok remember=5 file=/etc/security/opasswd",
                "snmpd -c /etc/snmp/snmpd.conf", "wheel:!::alice", "wheel:x:10:alice", '<entry key="timeout" value="30"/>',
                # the evidence it dropped (P2)
                "alice ALL=(root) /bin/id, NOPASSWD: /bin/cat", "AuthenticationMethods publickey,password",
                "authenticationmethods publickey,keyboard-interactive:pam publickey,password", "PasswordAuthentication no # site policy",
                '-A INPUT -p tcp -m comment --comment "snmp monitor"', "-A INPUT -i lo -j ACCEPT"]
        for text in kept:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), text)
        # redact_all: a value under a secret's name goes whole
        self.assertEqual(X.redact_all({"password": "BlueCanary", "credentials": ["a"], "name": "x", "cache_credentials": True}),
                         {"password": "<removed>", "credentials": "<removed>", "name": "x", "cache_credentials": True})

    def test_codex_review_of_8cae9b62(self):
        """1.77.0h: the second Codex pass on the release commit - the shapes a RHEL STIG check can print (the director's
        scope; the rest are known limits, docs/STIG_ANSWER_KEYS.md)."""
        removed = [("password BlueCanary # site policy", X.DROPPED),             # GRUB's line: a comment is no password
                   ("123svc:abJnggxhB/yWI:19000:0:99999:7:::", "123svc:<hash removed>:19000:0:99999:7:::"),
                   ("123ops:abJnggxhB/yWI:admin:alice", "123ops:<hash removed>:admin:alice"),
                   ("ApiKey BlueCanary", X.DROPPED), ("wep-key0 BlueCanary", X.DROPPED),
                   ("db_credentials=no", "db_credentials=<removed>"), ("db_credentials no", X.DROPPED)]
        for text, want in removed:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), want)
        kept = ["password root #Kq7wZx3v".replace("#Kq7wZx3v", "<removed>"), "123svc:x:1001:1001::/home/123svc:/bin/bash",
                "auth sufficient pam_sss.so forward_pass", "password optional pam_exec.so expose_authtok /usr/bin/check",
                "cache_credentials = True", "cache_credentials false"]
        for text in kept:
            with self.subTest(text=text):
                self.assertEqual(X.redact(text), text)
        # a GRUB password that starts with # is a password, not a comment
        self.assertEqual(X.redact("password root #Kq7wZx3v"), "password root <removed>")

    def test_codex_review_of_988d02a0(self):
        """1.77.0d: what Codex's review of 988d02a0 found, each now removed (or kept, for evidence)."""
        cases = [("password=alpha-12-bravo", "password=<removed>"),                         # a value like a grep prefix
                 ('{"endpoint":"http://server:8080","password":"first@second"}',            # a URL never runs into the next field
                  '{"endpoint":"http://server:8080","password":"<removed>"}'),
                 ("password: |- # provisioned locally\n  hunter2\nnext: 1", "password: <removed>\n  <removed>\nnext: 1"),
                 ("<password>\nhunter2\n</password>", "<password>\n<removed>\n</password>"),
                 ("db_password:!:123:tailsecret", "db_password:<removed>"),                   # not a whole shadow record
                 ('{"token":\n  "hunter2"\n}', '{"token":\n  "<removed>"\n}'),
                 ("credentials:\n- hunter2\n- two\nother: 1", "credentials:\n<removed>\n<removed>\nother: 1"),
                 ("    Service Password    REG_SZ    hunter2", "    Service Password    REG_SZ    <removed>"),
                 ("svc_token:abcdefghijklmnop:123:4:5", "svc_token:<removed>"),
                 ("- password: |\n    hunter2\n  enabled: true", "- password: <removed>\n    <removed>\n  enabled: true"),
                 # a whole shadow record: its hash removed on the host, the record kept on the second pass too
                 ("svc_token:$6$saltsalt$abcdefghijklmnop:19000:0:99999:7:::", "svc_token:$<hash removed>:19000:0:99999:7:::"),
                 ("    ServiceName    REG_SZ    x", "    ServiceName    REG_SZ    x")]
        for text, want in cases:
            for prefix in PREFIXES:
                line = grep(prefix, text)
                with self.subTest(text=line):
                    out = X.redact(line)
                    if prefix and text.startswith("<password>\n"):
                        # grep's prefix on each line inside the element goes with the value: more hidden, never less
                        self.assertNotIn("hunter2", out)
                    else:
                        self.assertEqual(out, grep(prefix, want))
                    self.assertEqual(X.redact(out), out)

    def test_codex_review_of_d39ab45e(self):
        """1.77.0d: what Codex's review of d39ab45e found - each value now removed whole, nothing after it left."""
        long_xml = "x" * 5000
        cases = [('{"token":\n"abc: def ghi"}', "abc"),                       # the next line's value: not read as a grep prefix
                 ('{"token":\n  "first second', "second"),                   # cut before its closing quote
                 ('{"token":\n  ["first", "second"]\n}', "second"),          # a list on the next line
                 ('{"token":\n  [\n    "first",\n    "second"\n  ],\n  "user": "u1"\n}', "second"),
                 ("https://bob:alpha;token=beta@example.mil", "alpha"),       # a name inside a URL's password
                 ("https://bob:" + "T" * 400 + "@host.example.mil/x", "TTTT"),  # a password over 256 characters
                 ("<token>\n" + long_xml + "\n</token>", "xxxx"),              # an element over 4096 characters
                 ("<password>\nfirst\nbindpw last</password>", "first"),      # a line inside it like a directive
                 ("credentials:\n# production account\n- first", "first"),     # a comment before the value
                 ("password: &saved |\n  first", "first"), ("password: !!str |\n  first", "first"),
                 ("/var/build-12-test/config-7-password=hunter2", "hunter2"),  # a digit-dash in grep's path
                 ("<password>\n a\n</password> <token>tokenvalue</token>", "tokenvalue")]
        for text, secret in cases:
            for prefix in PREFIXES:
                line = grep(prefix, text)
                with self.subTest(text=line):
                    out = X.redact(line)
                    self.assertNotIn(secret, out)
                    self.assertEqual(X.redact(out), out)
        # the rest of the JSON stays
        self.assertIn('"user": "u1"', X.redact(cases[3][0]))

    def test_codex_review_of_94a5aeaa(self):
        """1.77.0d: what Codex's review of 94a5aeaa found."""
        cases = [("token:\n  # local account\nplaintext", "plaintext"),          # the value after a comment line
                 ('{\n"token":\n  [\n"plaintext"\n]\n}', "plaintext"),            # a list opened on the next line
                 ("https://bob:alpha'token=beta@host.example.mil", "alpha"),     # an apostrophe in the password
                 ("<token><token>x</token>ysecret</token>", "ysecret"),           # nested elements
                 ("    REG_Name Password    REG_SZ    hunter2", "hunter2"),     # a false REG_ before the type
                 ("    Stra\u00dfe Password    REG_SZ    hunter2", "hunter2"),  # upper() would move the offsets
                 ("note token:x:1:2:private:/home:/bin/bash", "private"),         # not a record at the line's start
                 ('{ "token":\n[\n# ]\n"plaintext"\n]}', "plaintext")]          # a ] in a comment
        for text, secret in cases:
            for prefix in PREFIXES:
                line = grep(prefix, text)
                with self.subTest(text=line):
                    out = X.redact(line)
                    self.assertNotIn(secret, out)
                    self.assertEqual(X.redact(out), out)
        # the evidence the fixes keep
        self.assertEqual(X.redact('{"url":"http://server:8080","email":"user@example.mil"}'),
                         '{"url":"http://server:8080","email":"user@example.mil"}')
        self.assertEqual(X.redact("<password />\nPermitRootLogin no"), "<password />\nPermitRootLogin no")

    def test_the_backstop_drops_a_line_it_does_not_understand(self):
        """1.77.0d: a secret's word left on a line the first layer did not understand - the whole line goes."""
        for line in ("my password is hunter2 today", "community string public", "Token hunter2", "ldap_default_authtok_type = hunter2",
                     "seed $6$", "keytab: data", "a PRIVATE KEY follows", "AuthorizedPassphrase hunter2"):
            for prefix in ("", "/etc/x.conf:", "/etc/x.conf:12:", "/etc/x.conf-12-", "12:"):
                with self.subTest(line=prefix + line):
                    self.assertEqual(X.redact(prefix + line), prefix + X.DROPPED)
            with self.subTest(line="word:" + line):          # a prefix that is no path nor number goes too
                self.assertEqual(X.redact("word:" + line), X.DROPPED)
        self.assertEqual(X.redact("ok\nmy password is x\nok"), "ok\n" + X.DROPPED + "\nok")
        # a command line (DISA's text): the first layer only
        self.assertEqual(X.redact("grep -i password /etc/pam.d/system-auth", backstop=False), "grep -i password /etc/pam.d/system-auth")

    def test_the_backstop_keeps_stig_evidence(self):
        """What the STIG checks read stays as it is: the director's list, and the shapes added for common checks."""
        lines = ["password    requisite     pam_pwquality.so retry=3", "-password   optional    pam_gnome_keyring.so use_authtok",
                 "auth        sufficient    pam_unix.so try_first_pass", "-w /etc/passwd -p wa -k identity",
                 "-a always,exit -F path=/usr/bin/passwd -F perm=x -F auid>=1000 -F auid!=unset -k privileged-passwd",
                 "auditctl -a always,exit -F path=/usr/bin/chage -k privileged-passwd",
                 "passwd: files sss", "passwd:     files systemd [NOTFOUND=return] altfiles",
                 "root:x:0:0:root:/root:/bin/bash", "svc_token:x:1001:1001::/home/svc_token:/bin/bash",
                 "locked:!!:19000:0:99999:7:::", "svc_token:*:19000:0:99999:7:::", "svc_token:$<hash removed>:19000:0:99999:7:::",
                 "PermitEmptyPasswords no", "PasswordAuthentication no", "permitemptypasswords no", "PermitRootLogin prohibit-password",
                 "PermitRootLogin without-password", "cache_credentials = True", "offline_credentials_expiration = 1",
                 "HostKey /etc/ssh/ssh_host_rsa_key", "authorizedkeysfile .ssh/authorized_keys", "-rw-r-----. 1 root root 1234 Nov 18 10:00 /etc/shadow",
                 "Last password change                                    : Jan 01, 2026", "Password expires                                        : never",
                 "Minimum number of days between password change          : 1",
                 "root PS 2026-01-01 1 60 7 -1 (Password set, SHA512 crypt.)", "passwd-0.80-4.el8.x86_64", "package passwd is not installed",
                 "Keytab name: FILE:/etc/krb5.keytab", "ls: cannot access '/etc/*.keytab': No such file or directory",
                 "GRUB2_PASSWORD=grub.pbkdf2.<hash removed>", "db_password=<removed>", '{"token": "<removed>", "user": "u1"}',
                 "https://bob:<removed>@host.example.mil/x", "<password><removed></password>", "bindpw <removed>",
                 "    DefaultPassword    REG_SZ    <removed>", "-----BEGIN RSA " "PRIVATE KEY----- <removed> -----END RSA PRIVATE KEY-----",
                 "password_pbkdf2 root grub.pbkdf2.<hash removed>", "ENCRYPT_METHOD SHA512", "minlen = 15"]
        for prefix in ("", "/etc/x.conf:", "/etc/x.conf:12:", "/etc/x.conf-12-"):
            for line in lines:
                with self.subTest(line=prefix + line):
                    self.assertEqual(X.redact(prefix + line), prefix + line)

    def test_evidence_unchanged(self):
        for prefix in PREFIXES:
            lines = [prefix + line for line in EVIDENCE]
            for text in lines + ["\n".join(lines)]:
                with self.subTest(text=text):
                    out = X.redact(text)
                    if out != text:
                        self.failures.append((text, out))
                    self.assertEqual(out, text)


if __name__ == "__main__":
    program = unittest.main(exit=False)
    print("Failing cases (input -> output; JSON strings, one line each):")
    for failed_input, failed_output in RedactionTable.failures:
        print(json.dumps(failed_input) + " -> " + json.dumps(failed_output))
    sys.exit(not program.result.wasSuccessful())
