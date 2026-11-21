"""1.77.0b: the one redaction (plugins/module_utils/site_redact.py) - each kind of secret removed, the evidence around it
kept, and every path of the STIG answer keys jobs using it (the host modules, the filters)."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
sys.path.insert(0, os.path.join(REPO, "roles", "check_stig", "library"))
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))
import site_redact as X  # noqa: E402


def red(text):
    return X.redact(text)


class Secrets(unittest.TestCase):
    def test_crypt_hashes(self):
        for h in ("$1$salt$abcdefgh", "$2b$12$abcdefghijABCDEFGHIJabcdefghi", "$5$rounds=5000$s$abc", "$6$saltsalt$abcDEF/x.y",
                  "$y$j9T$salt$hashhash", "$argon2id$v=19$m=65536$abc", "$pbkdf2-sha256$29000$abc$def"):
            with self.subTest(h=h):
                out = red("x " + h + " y")
                self.assertNotIn(h[4:], out)
                self.assertIn("<hash removed>", out)

    def test_grub_pbkdf2(self):
        for line in ("GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AABBCC.DDEEFF",
                     "password_pbkdf2 root grub.pbkdf2.sha512.10000.AB12.CD34",
                     "/boot/grub2/user.cfg:GRUB2_PASSWORD=grub.pbkdf2.sha512.10000.AABBCC.DDEEFF"):
            with self.subTest(line=line):
                out = red(line)
                self.assertNotIn("sha512.10000", out)
                self.assertIn("grub.pbkdf2.<hash removed>", out)
        self.assertEqual(red("GRUB2_PASSWORD=grub.pbkdf2.sha512.1.AB.CD"), "GRUB2_PASSWORD=grub.pbkdf2.<hash removed>")

    def test_a_plain_grub_password_but_never_a_pam_line(self):
        self.assertEqual(red("password root plainsecret"), "password root <removed>")
        for pam in ("password    requisite     pam_pwquality.so retry=3", "password    [success=1 default=ignore] pam_unix.so sha512",
                    "password sufficient pam_unix.so", "password include system-auth"):
            with self.subTest(pam=pam):
                self.assertEqual(red(pam), pam)

    def test_shadow_like_lines(self):
        self.assertEqual(red("root:$6$salt$abcDEF/x.y:19000::"), "root:$<hash removed>:19000::")
        self.assertEqual(red("olduser:abcdefghijklm:19000:0:99999:7:::"), "olduser:<hash removed>:19000:0:99999:7:::")
        self.assertEqual(red("locked:!$6$x$yyyyyyyy:19000:"), "locked:!$<hash removed>:19000:")
        self.assertEqual(red("/etc/shadow:svc:ABCDEFGHIJKLMNOP:19000:0:::"), "/etc/shadow:svc:<hash removed>:19000:0:::")
        # a locked or empty password, and /etc/passwd's x: evidence, kept
        for keep in ("bin:*:19000:0:99999:7:::", "user1:!!:19000:0:99999:7:::", "nopw::19000:0:99999:7:::", "/etc/passwd:root:x:0:0:root:/root:/bin/bash"):
            with self.subTest(keep=keep):
                self.assertEqual(red(keep), keep)

    def test_pem_private_keys(self):
        for kind in ("RSA PRIVATE KEY", "PRIVATE KEY", "OPENSSH PRIVATE KEY", "EC PRIVATE KEY", "ENCRYPTED PRIVATE KEY"):
            with self.subTest(kind=kind):
                out = red("before\n-----BEGIN %s-----\nMIIsecretline1\nsecretline2\n-----END %s-----\nafter" % (kind, kind))
                self.assertNotIn("secretline", out)
                self.assertTrue(out.startswith("before\n") and out.endswith("\nafter"))
        self.assertNotIn("MIIcut", red("-----BEGIN " "PRIVATE KEY-----\nMIIcut"))          # cut by out_max: to the end
        cert = "-----BEGIN CERTIFICATE-----\nMIIpublic\n-----END CERTIFICATE-----"
        self.assertEqual(red(cert), cert)

    def test_named_values(self):
        cases = {"db_password: \"hunter2\"": "db_password: \"<removed>\"", "api_token=abc123": "api_token=<removed>",
                 "{\"client_secret\": \"xyz\", \"name\": \"n\"}": "{\"client_secret\": \"<removed>\", \"name\": \"n\"}",
                 "ssl_key = /etc/pki/k.pem": "ssl_key = <removed>", "PASSWORD='p a s s'": "PASSWORD='<removed>'",
                 "bind_passwd=x1": "bind_passwd=<removed>", "passphrase: abc": "passphrase: <removed>",
                 "aws_secret_access_key=AKIA": "aws_secret_access_key=<removed>", "apikey=k": "apikey=<removed>",
                 "credentials=c": "credentials=<removed>", "snmp_community=public": "snmp_community=<removed>"}
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(red(line), want)

    def test_ldap_and_sssd_bind_passwords(self):
        self.assertEqual(red("ldap_default_authtok = Pa55word"), "ldap_default_authtok = <removed>")
        # 1.77.0d: the line backstop - a secret's word with a value not understood: the whole line goes. 1.77.0f: the
        # type's two values are evidence (a STIG check reads it), kept
        self.assertEqual(red("ldap_default_authtok_type = hunter2"), X.DROPPED)
        self.assertEqual(red("ldap_default_authtok_type = obfuscated_password"), "ldap_default_authtok_type = obfuscated_password")
        self.assertEqual(red("bindpw s3cret"), "bindpw <removed>")
        self.assertEqual(red("rootpw {SSHA}abcdef"), "rootpw <removed>")

    def test_keytab_keys(self):
        line = "   2 host/web01.example.mil@EXAMPLE.MIL (aes256-cts-hmac-sha1-96)  (0x" + "00112233445566778899aabbccddeeff" * 2 + ")"
        out = red(line)
        self.assertNotIn("00112233", out)
        self.assertIn("host/web01.example.mil@EXAMPLE.MIL (aes256-cts-hmac-sha1-96)  (0x<removed>)", out)

    def test_snmp_communities(self):
        self.assertEqual(red("rocommunity public default"), "rocommunity <removed>")      # 1.77.0d: the rest of the line
        self.assertEqual(red("rwcommunity6 private"), "rwcommunity6 <removed>")
        self.assertEqual(red("com2sec readonly default public"), "com2sec readonly default <removed>")
        self.assertEqual(red('createUser myuser SHA "authpass1" AES "privpass1"'), "createUser myuser <removed>")


class Shapes(unittest.TestCase):
    """1.77.0c (Codex): the shapes 1.77.0b let through."""

    def test_a_grep_prefix_before_a_directive(self):
        cases = {"/etc/snmp/snmpd.conf:rocommunity private default": "/etc/snmp/snmpd.conf:rocommunity <removed>",
                 "12:bindpw secret": "12:bindpw <removed>", "/etc/openldap/slapd.conf:7:rootpw {SSHA}abc": "/etc/openldap/slapd.conf:7:rootpw <removed>",
                 "/etc/sssd/sssd.conf-3-ldap_default_authtok = Pa55": "/etc/sssd/sssd.conf-3-ldap_default_authtok = <removed>",
                 "snmpd.conf:com2sec ro default public": "snmpd.conf:com2sec ro default <removed>",
                 "snmpd.conf:4:createUser u SHA a1 AES p1": "snmpd.conf:4:createUser u <removed>",
                 "/boot/grub2/grub.cfg:password root plain": "/boot/grub2/grub.cfg:password root <removed>", "12-db_password=x": "12-db_password=<removed>"}
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(red(line), want)
        for pam in ("/etc/pam.d/system-auth:password    sufficient pam_unix.so sha512", "/etc/pam.d/system-auth:12:password [success=1] pam_unix.so",
                    "-password optional pam_gnome_keyring.so"):
            with self.subTest(pam=pam):
                self.assertEqual(red(pam), pam)

    def test_passwd_with_a_value_that_is_not_nss_sources(self):
        for line in ("passwd: hunter2", "passwd: files hunter2", "/etc/nsswitch.conf:passwd: hunter2", 'passwd: "x y"'):
            with self.subTest(line=line):
                self.assertNotIn("hunter2", red(line))
                self.assertNotIn("x y", red(line))
                self.assertIn("<removed>", red(line))
        for keep in ("passwd:     files sss", "passwd: files systemd [NOTFOUND=return] altfiles", "/etc/nsswitch.conf:passwd: sss files",
                     "passwd: compat", "passwd: files [!UNAVAIL=return] ldap winbind"):
            with self.subTest(keep=keep):
                self.assertEqual(red(keep), keep)

    def test_a_quoted_value_with_spaces_goes_whole(self):
        cases = {'bindpw "first second"': 'bindpw "<removed>"', "bindpw 'a b c'": "bindpw '<removed>'",
                 'rocommunity "my comm" default': 'rocommunity "<removed>"', 'ldap_default_authtok "pa ss"': 'ldap_default_authtok "<removed>"',
                 'password root "a b"': 'password root "<removed>"', 'db_password = "a b c"': 'db_password = "<removed>"',
                 "client_secret: 'x y z'": "client_secret: '<removed>'", 'com2sec ro default "pub lic"': 'com2sec ro default "<removed>"',
                 # its closing quote missing (cut by whatever printed it): to the line's end, the quote closed
                 'token: "abc def': 'token: "<removed>"', "bindpw 'first second\nnext line": "bindpw '<removed>'\nnext line",
                 # 1.77.0d: a value not a JSON string is the rest of its line
                 "password: correct horse battery": "password: <removed>", "client_secret: abc def, user: bob": "client_secret: <removed>"}
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(red(line), want)
                self.assertEqual(red(red(line)), want)

    def test_redacted_whole_then_cut(self):
        # a cut inside the quotes: before 1.77.0c the half after the cut was left bare (bindpw "first sec)
        head = "x" * 180 + '\nbindpw "first '
        text = head + 'second third"\nmore'
        self.assertEqual(len(head), 195)
        import site_stig_scan as scan
        st = {"rules": [{"rule": "SV-9r1_rule", "v": "V-9", "stigid": "X", "severity": "medium", "title": "T", "result": "pass",
                         "details": text, "comments": text}]}
        it = scan.checklist_items(st, details_max=200, texts=True)[0]
        for k in ("details", "comments"):
            with self.subTest(k=k):
                self.assertTrue(it[k].startswith(head[:181] + 'bindpw "<removed>'), it[k])
                for word in ("first", "secon", "third"):
                    self.assertNotIn(word, it[k])
        import site_stig_answer_reads as reads
        r = reads.run_pipeline([["/usr/bin/printf", "%s", text]], 10, 200)
        self.assertEqual(r["rc"], 0)
        self.assertTrue(r["out"].startswith(head[:181] + 'bindpw "<removed>"'), r["out"])
        self.assertTrue(r["out"].endswith("(cut at 200 bytes)"))
        for word in ("first", "secon", "third"):
            self.assertNotIn(word, r["out"])


class Evidence(unittest.TestCase):
    """What a STIG check reads stays readable."""

    def test_evidence_kept(self):
        for line in ("-a always,exit -F path=/usr/bin/passwd -F perm=x -F auid>=1000 -F key=privileged-passwd",
                     "-w /etc/shadow -p wa -k identity", "passwd:     files sss", "shadow:     files", "PASS_MAX_DAYS   60",
                     "PubkeyAuthentication yes", "HostKey /etc/ssh/ssh_host_rsa_key", "kernel.keys.maxkeys = 200",
                     "ENCRYPT_METHOD SHA512", "/etc/security/opasswd:user:1000:2:x,y", "minlen = 15",
                     "-rw-r-----. 1 root root 1234 Nov 18 10:00 /etc/shadow", "ActiveState=active", "PermitRootLogin no"):
            with self.subTest(line=line):
                self.assertEqual(red(line), line)

    def test_not_a_string_and_nested(self):
        self.assertEqual(red(None), "")
        self.assertEqual(red(5), "5")
        v = X.redact_all({"a": ["root:$6$s$abcdefgh:1:", {"b": "token=x"}], "n": 3})
        self.assertEqual(v, {"a": ["root:$<hash removed>:1:", {"b": "token=<removed>"}], "n": 3})

    def test_twice_is_the_same(self):
        s = "GRUB2_PASSWORD=grub.pbkdf2.sha512.1.AB.CD\nroot:$6$s$abcdefgh:1:\ntoken=x"
        self.assertEqual(red(red(s)), red(s))


class EveryPath(unittest.TestCase):
    """The host modules import it and use it before a text leaves the host; the filters load the same file."""

    def read(self, *p):
        with open(os.path.join(REPO, *p)) as f:
            return f.read()

    def test_the_host_modules(self):
        for mod, uses in (("site_stig_answer_reads.py", ["return redact(text)", "text = mask(head(text, max(READ_MAX, out_max)))", "shown = redact(cmd, backstop=False)",
                                                         "redact(ln, backstop=False)[:200]", '"out": mask(str(e))']),
                          ("site_stig_answer_host.py", ['"read": redact(', '"error": redact(', '"error": redact(err)[:300]',
                                                        'redact(", ".join(str(v) for v in vals[:3]))[:300]',
                                                        'redact(p.stderr.decode("utf-8", "replace").strip() or "pwsh gave no answer")[:300]',
                                                        'redact(r.stderr.decode("utf-8", "replace").strip() or "no answer")[:200]',
                                                        'redact("; ".join(moved))[:240]',
                                                        # 1.77.0: why a code is not tried - the sandbox, a path, a command
                                                        'redact(no_sandbox)[:300]', '"error": redact(why_blind)[:300]', '"why": redact(no_sandbox or "")[:300]',
                                                        'head(p.stderr.decode("utf-8", "replace"), 65536)',
                                                        'redact(bad[0], backstop=False)[:200]']),
                          ("site_stig_scan.py", ['by[key]["details"] = redact(', 'by[key]["comments"] = redact(',
                                                 "why=redact(why)", "return redact(raw), evaluate_said(raw)", "for ln in redact(text",
                                                 'err = redact(proc.stderr', "result[k] = redact(result[k])"]),
                          ("site_stigman_read.py", ["redact(body.strip())[:300]", "body = head(body, len(body) - 1)",
                                                    'redact(str(e.reason or ""))[:200]', 'could not be read: %s" % redact(str(e))',
                                                    "redact(str(e.reason))[:200]", 'info["error"] = redact(str(e))[:200]',
                                                    '"why": redact(str(e))'])):
            t = self.read("roles", "check_stig", "library", mod)
            with self.subTest(mod=mod):
                self.assertRegex(t, r"(?m)^from ansible\.module_utils\.site_redact import (?:[a-z_]+, )*redact\b")
                # 1.77.0h: an unexpected error redacted, never a raw traceback in the job's output
                self.assertIn("run_main(main)", t)
                for u in uses:
                    self.assertIn(u, t)
        self.assertNotRegex(self.read("roles", "check_stig", "library", "site_stig_answer_reads.py"), r"(?m)^HASH = ")

    def test_a_moved_path_is_kept(self):
        # 1.76.1's "a path changed during the trial (...)": paths only, so redact leaves it as it is
        moved = "/etc/passwd: /etc/passwd -> /var/x; /etc/ssh/ssh_host_rsa_key: /etc/ssh/ssh_host_rsa_key -> None; /x: /a -> /b"
        self.assertEqual(X.redact(moved), moved)

    def test_review_of_d5801557_call_sites(self):
        """1.77.0h (Codex on d5801557): a text read in part, an unexpected error, a task's raw failure."""
        import io
        import json
        from contextlib import redirect_stdout
        # head(): to a line's end - the line a cut would split is left out whole
        text = "ok line\nhttps://u:" + "A" * 100 + "@host"
        self.assertEqual(X.head(text, 50), "ok line")
        self.assertEqual(X.head("https://u:" + "A" * 100, 50), "")
        self.assertEqual(X.head("short", 50), "short")
        self.assertNotIn("AAAA", X.redact(X.head(text, 50)))
        # run_main: an error a module did not expect - its type and redacted text, no traceback

        def boom():
            raise ValueError("bad line: password=BlueCanary")
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            X.run_main(boom)
        self.assertEqual(json.loads(out.getvalue()), {"failed": True, "msg": "ValueError: bad line: password=<removed>"})
        # the tasks whose raw failure Ansible would print: no_log (the rescue shows it redacted)
        for f, task in (("answer_keys_cross.yml", "site_stig_answer_reads:"), ("answer_keys_check.yml", "site_stig_answer_host:"),
                        ("answer_keys.yml", "ansible.builtin.setup:")):
            t = self.read("roles", "check_stig", "tasks", f)
            i = t.index(task)
            with self.subTest(f=f):
                self.assertRegex(t[i:i + 900], r"\n *no_log: true\n")

    def test_why_a_code_is_not_tried_is_kept(self):
        # 1.77.0: the trial's reasons (the sandbox probe's, a place it cannot show, a command not replayed) are redacted
        # on the host; their own words are never a secret's, so redact leaves each as it is
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "site_stig_answer_host", os.path.join(REPO, "roles", "check_stig", "library", "site_stig_answer_host.py"))
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        texts = ["it reads %s - the trial cannot show it as the host has it" % w for _, w in h.BLIND]
        texts += ["it reads %s - the trial cannot show %s as the host has it (Get-Content /etc/x)" % (v, k) for k, v in h.PLACE.items()]
        src = self.read("roles", "check_stig", "library", "site_stig_answer_host.py")
        texts += [r.replace("%s", "/run/x").replace("%d", "3") for r in re.findall(r'return "([^"]{8,})"', src)]
        self.assertGreater(len(texts), 30)
        for t in texts:
            with self.subTest(t=t):
                self.assertEqual(X.redact(t), t)
        # a command line keeps its words, its secret goes
        self.assertEqual(X.redact("/usr/bin/snmpget -v3 -l authPriv -A AuthPass123 h", backstop=False),
                         "/usr/bin/snmpget -v3 -l authPriv -A <removed> h")

    def test_the_filters(self):
        import stig_answer_cross_filters as C
        import stig_answer_filters as A
        import stig_answer_try_filters as T
        for m in (A, C, T):
            with self.subTest(m=m.__name__):
                self.assertEqual(os.path.basename(m._redact.__file__), "site_redact.py")
                self.assertEqual(m.redact("token=x"), "token=<removed>")
        cross = self.read("plugins", "filter", "stig_answer_cross_filters.py")
        # 1.77.0d: the evidence (its command too), ACT's answer, and every reason a host or rule was not asked
        for u in ('redact(_s(e.get("cmd")), backstop=False)', 'redact(_s(e.get("out")))', "redact(_s(text))", 'redact(_s(hr["failed"]))[:200]',
                  'redact(_s(x.get("why")))', 'redact(_s(hs[0].get("code_why")))'):
            self.assertIn(u, cross)
        self.assertIn('redact(_s(a.get("why") or a.get("msg")))', self.read("plugins", "filter", "stig_answer_try_filters.py"))
        self.assertIn("redact(es_why)", self.read("plugins", "filter", "stig_answer_filters.py"))
        self.assertIs(A.FilterModule().filters()["site_redact"], A.redact)

    def test_a_task_that_cuts_an_error_redacts_it_first(self):
        """1.77.0d: an error text a task cuts ([:300]) goes through the site_redact filter before the cut."""
        tasks = os.path.join(REPO, "roles", "check_stig", "tasks")
        for name in sorted(os.listdir(tasks)):
            if not name.startswith("answer_keys"):
                continue
            for n, ln in enumerate(self.read("roles", "check_stig", "tasks", name).splitlines(), 1):
                if re.search(r"\.msg \| default", ln):
                    with self.subTest(task="%s:%d" % (name, n)):
                        self.assertRegex(ln, r"\| string \| site_redact\)+\[:\d+\]")


# the fragments of every secret below: none of them may be left anywhere in what redact returns
FRAG = ("Kq7w", "Zx3v")
# a character or marker that once ended a value early, or was taken for one already removed
SPECIALS = [",", ";", ":", "]", "}", ")", "=", "#", " ", "\t", "\"", "'", "\\", "@", "&", "[", "{", "(", "|", "/", "$", "<", ">",
            "<removed>", "<hash removed>", "$<hash removed>", "grub.pbkdf2.<hash removed>", "$6$salt$"]


def secrets():
    a, b = FRAG
    for c in SPECIALS:
        yield a + c + b
        yield c + a + b
        yield a + b + c
        yield a + c + c + b + c + a
    yield "prefix $6$salt$" + a + b + " suffix"
    yield "<removed> " + a + b


def _mark(v):
    """What a value becomes: <removed>, in its quotes when it starts with one; a value that is one whole crypt hash and
    nothing else, $<hash removed> (which kind of hash it is, is evidence)."""
    q = v[0] if v[:1] in ("\"", "'") else ""
    inner = v[1:-1] if q and len(v) > 1 and v[-1] == q else v[len(q):]
    whole_hash = inner.startswith("$6$salt$") and not re.search(r"[\s:'\"]", inner)
    return q + ("$" + X.HASH_MARK if whole_hash else X.MARK) + q


def _dq(s):
    return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""


def _sq(s):
    return "'" + s.replace("'", "'\\''") + "'"


# a secret's line: what comes before its value (the value is the rest of the line)
REST_HEADS = ["password=", "password = ", "export DB_PASSWORD=", "DB_PASS=", "password: ", "  Password : ", "client_secret=",
              "api-key: ", "--token=", "/x?a=1&access_token=", "12:password=", "/etc/sssd/sssd.conf:12:ldap_default_authtok = ",
              "/etc/x.conf-3-db_password=", "pwd:", "- passphrase: ", "$env:API_TOKEN = ", "{'password': ", "\"password\"= ",
              "{\\\"password\\\":", "bindpw ", "rootpw ", "/etc/nslcd.conf:7:bindpw ", "ldap_default_authtok ", "rocommunity ",
              "rwcommunity6 ", "com2sec notConfigUser default ", "com2sec6 -Cn ctx ro default ", "password root ",
              "/boot/grub2/user.cfg:GRUB2_PASSWORD=", "    DefaultPassword    REG_SZ    "]
# a JSON "name": "value" - the value ends where its JSON string ends, the rest of the line kept
JSON_LINES = [("{\"user\": \"u\", \"password\": %s, \"ok\": true}", "{\"user\": \"u\", \"password\": \"<removed>\", \"ok\": true}"),
              ("{\"password\":%s,\"next\":\"n1\"}", "{\"password\":\"<removed>\",\"next\":\"n1\"}"),
              ("[{\"client_secret\" : %s}]", "[{\"client_secret\" : \"<removed>\"}]"),
              ("{\"a\": {\"api_key\": %s, \"token\": %s}}", "{\"a\": {\"api_key\": \"<removed>\", \"token\": \"<removed>\"}}")]


class EveryShape(unittest.TestCase):
    """1.77.0d (Codex, round 3): when in doubt, more is removed. Every secret through every shape: not one of its fragments
    left, the line exactly as expected, and redacting twice the same."""

    def check(self, line, want):
        out = red(line)
        for f in FRAG:
            self.assertNotIn(f, out, line)
        self.assertEqual(out, want, line)
        self.assertEqual(red(out), out, line)

    def test_the_rest_of_the_line(self):
        n = 0
        for s in secrets():
            for v in (s, _dq(s), _sq(s), s + "  # a note"):
                for head in REST_HEADS:
                    core = v.strip(" \t")
                    lead, trail = v[:len(v) - len(v.lstrip(" \t"))], v[len(v.rstrip(" \t")):]
                    with self.subTest(head=head, v=v):
                        self.check(head + v, head + lead + _mark(core) + trail)
                        n += 1
        self.assertGreater(n, 10000)

    def test_a_json_string(self):
        import json
        for s in secrets():
            for line, want in JSON_LINES:
                with self.subTest(line=line, s=s):
                    self.check(line % ((json.dumps(s),) * line.count("%s")), want.replace('"<removed>"', _mark(json.dumps(s))))

    def test_whole_values_of_their_own(self):
        for s in secrets():
            with self.subTest(s=s):
                self.check("createUser bob SHA " + s, "createUser bob <removed>")
                self.check("<password>%s</password> after" % s, "<password>%s</password> after" % _mark(s))
                self.check("db:\n  password: |\n    %s\n    %s\nnext: 1" % (s.strip(), s.strip()),
                           "db:\n  password: <removed>\n    <removed>\n    <removed>\nnext: 1")
                if not re.search(r"\s", s):          # a URL has no blank in it
                    self.check("see https://bob:%s@host.example.mil/x" % s, "see https://bob:<removed>@host.example.mil/x")


class Round3(unittest.TestCase):
    """1.77.0d (Codex): the four findings, one test each."""

    def test_a_value_not_quoted_goes_to_the_line_end(self):
        for c in ",;:]})=# ":
            with self.subTest(c=c):
                self.assertEqual(red("password=first%ssecond" % c), "password=<removed>")
                self.assertEqual(red("x: 1\napi_token: first%ssecond\ny: 2" % c), "x: 1\napi_token: <removed>\ny: 2")

    def test_only_a_whole_marker_is_skipped(self):
        self.assertEqual(red('password="prefix $6$salt$hashhash suffix"'), 'password="<removed>"')
        self.assertEqual(red("password=<removed>x"), "password=<removed>")
        self.assertEqual(red("password=a<hash removed>b"), "password=<removed>")
        self.assertEqual(red("token: <removed> and more"), "token: <removed>")
        for done in ("password=<removed>", 'password="<removed>"', "password=$<hash removed>", "password=grub.pbkdf2.<hash removed>",
                     'password=""'):
            with self.subTest(done=done):
                self.assertEqual(red(done), done)
        # a whole hash says which kind it is - that is evidence; anything around it, the whole value goes
        self.assertEqual(red("password=$6$salt$abcdefgh"), "password=$<hash removed>")
        self.assertEqual(red("password=$6$salt$abcdefgh extra"), "password=<removed>")

    def test_the_scanners_words_redacted_before_the_tail_is_cut(self):
        import tempfile
        import unittest.mock
        import site_stig_scan as scan
        import stig_answer_try_filters as T
        d = tempfile.mkdtemp()
        sh = os.path.join(d, "Evaluate-STIG_Bash.sh")
        # Evaluate-STIG writes no checklist; its last line holds a quoted secret longer than the 600 characters kept
        with open(sh, "w") as f:
            f.write("#!/bin/bash\necho 'Evaluate-STIG started'\necho 'ERROR: the bind failed, password=\"Kq7w %s\"'\n" % ("Zx3v" * 200))
        m = FakeModule(dir=d, evaluate=sh)
        with unittest.mock.patch.object(scan, "os_release", return_value={"VERSION_ID": "9.6"}):
            out = scan.Scan(m).run()
        self.assertFalse(out["scanned"])
        self.assertTrue(out["why"].startswith("Evaluate-STIG wrote no checklist (exit 0): "), out["why"])
        for k in ("why", "stderr"):
            with self.subTest(k=k):
                for f in FRAG:
                    self.assertNotIn(f, out[k])
                self.assertIn('password="<removed>"', out[k])
        # the filter, given a why a host did not redact (an older module): redacted there too
        t = T.stig_answer_try_rows([{"host": "h1", "without": {"scanned": False, "why": 'ERROR: password="Kq7w Zx3v"'}, "with": {}}])
        self.assertEqual(t["not_run"], [{"host": "h1", "why": 'without answer files: ERROR: password="<removed>"',
                                         "why_mail": "Evaluate-STIG did not run without answer files."}])

    def test_every_other_free_text_of_the_filters(self):
        import stig_answer_cross_filters as C
        import stig_answer_filters as A
        a = C.stig_answer_cross_act_answer("Open\nThe file has token=Kq7w Zx3v in it")
        self.assertEqual(a, {"verdict": "Open", "why": "The file has token=<removed>", "decides": ""})
        # 1.88.0: the deciding line ACT names is redacted the same way
        a = C.stig_answer_cross_act_answer("Open\nwhy\ntoken=Kq7w Zx3v")
        self.assertEqual(a["decides"], "token=<removed>")
        task = C.stig_answer_cross_act_task({"v": "V-1"}, [{"cmd": "grep -r password=Kq7w /etc", "rc": 1, "out": ""}])
        self.assertNotIn("Kq7w", task)
        asks = C.stig_answer_cross_asks([{"label": "L", "rep": "h1", "rules": [{"v": "V-1", "hosts": ["h1"]}]}],
                                        {"h1": {"failed": "it failed: password=Kq7w Zx3v"}})
        self.assertNotIn("Kq7w", str(asks))
        p = A.stig_answer_pick([{"v": "V-1"}], [], es_why="ERROR: secret: Kq7w Zx3v")
        self.assertEqual(p[0]["why"], "Not drafted - Evaluate-STIG could not run only the picked rules: ERROR: secret: <removed>")


class FakeModule(object):
    """site_stig_scan's AnsibleModule, as tests/test_stig.py has it."""
    check_mode = False

    def __init__(self, **params):
        self.params = dict(dir="", keep=10, save=True, keep_report=True, keep_results=True, oscap="", content_dir="", datastream="",
                           profile="stig", tailoring="", timeout=60, scanner="evaluate_stig", evaluate="", evaluate_select={},
                           evaluate_args=[], evaluate_read="", evaluate_output="CKLB", evaluate_rejected=True, pwsh="",
                           benchmarks={"8": "RHEL_8_STIG", "9": "RHEL_9_STIG", "10": "RHEL_10_STIG"},
                           vulns=[], history=True, answers="", answers_arg="--AFPath", answers_error="", answers_source="", answers_count=0)
        self.params.update(params)


if __name__ == "__main__":
    unittest.main()
