#!/usr/bin/env python3
"""Tests for tools/aap-nginx-blocks.py (1.51.0): the nginx blocks for AAP, Keycloak's card and the report website,
written from the settings into a folder of its own. Each kind of block has the lines it needs, the script writes
two files and nothing else, it refuses nginx's own folders and a file it did not write, a setting cannot smuggle a
line into nginx's file, and the guide shows what the script writes. With nginx installed, every kind passes
"nginx -t". python3 tests/test_aap_nginx_blocks.py"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO, "tools", "aap-nginx-blocks.py")
REALM = os.path.join(REPO, "tools", "aap-keycloak-realm.py")
CHECK = os.path.join(REPO, "tools", "aap-keycloak-check.sh")
EXAMPLE = os.path.join(REPO, "tools", "aap-keycloak.env.example")
DOC = os.path.join(REPO, "docs", "AAP_KEYCLOAK.md")
BASE = {"AAP_URL": "https://aap.yoursite.mil", "AAP_UPSTREAM": "https://aap01.yoursite.mil:443",
        "KEYCLOAK_URL": "https://keycloak.yoursite.mil", "STIGMAN_URL": "https://stigman.yoursite.mil"}
FOLDER = {"REPORT_WEB_FOLDER": "/srv/site-reports"}
CA = {"NGINX_CLIENT_CA": "/etc/nginx/tls/dod-ca-chain.pem"}


def read(p):
    with open(p) as f:
        return f.read()


def load(path=SCRIPT, name="aap_nginx_blocks"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = load()


def blocks(**env):
    s = MOD.read_settings("", dict(BASE, **env))
    MOD.check_settings(s)
    return MOD.blocks_text(s), MOD.steps_text(s)


def part(text, begin):
    """The lines between one begin line and its end line."""
    lines = text.splitlines()
    start = [i for i, ln in enumerate(lines) if ln.startswith("# --- %s: begin" % begin)]
    assert len(start) == 1, (begin, start)
    end = [i for i, ln in enumerate(lines) if i > start[0] and re.match(r"# --- .*: end ---$", ln)][0]
    return "\n".join(lines[start[0] + 1:end])


class TheBlocks(unittest.TestCase):

    def test_block_a_has_what_aap_needs(self):
        text, _ = blocks()
        a = part(text, "AAP")
        for line in ("server_name aap.yoursite.mil;", "listen 443 ssl;", "ssl_verify_client off;", "proxy_http_version 1.1;",
                     "proxy_pass https://aap01.yoursite.mil:443;", "proxy_ssl_server_name on;",
                     "proxy_set_header Host $http_host;", "proxy_set_header X-Forwarded-Proto https;",
                     "proxy_set_header X-Forwarded-Host $http_host;",
                     "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                     "proxy_set_header Upgrade $http_upgrade;", "proxy_set_header Connection $connection_upgrade;",
                     "client_max_body_size 200m;", "proxy_read_timeout 600s;",
                     "ssl_certificate     /etc/nginx/tls/aap-fullchain.pem;", "ssl_certificate_key /etc/nginx/tls/aap-key.pem;",
                     "map $http_upgrade $connection_upgrade {"):
            self.assertIn(line, a, line)
        self.assertNotIn("ssl-client-cert", a, "AAP never sees the card")
        self.assertNotIn("ssl_client_certificate", a)

    def test_every_block_is_whole(self):
        for env in ({}, FOLDER, dict(FOLDER, REPORTS="server", REPORTS_URL="https://reports.yoursite.mil", **CA),
                    dict(KEYCLOAK_NGINX="server", KEYCLOAK_UPSTREAM="http://keycloak:8080", **CA),
                    dict(KEYCLOAK_NGINX="off"), {"REPORTS_UPSTREAM": "http://web01.yoursite.mil:8080"}):
            text, steps = blocks(**env)
            self.assertEqual(text.count(": begin ("), len(re.findall(r"(?m)^# --- .*: end ---$", text)), env)
            code = "\n".join(ln.split("#")[0] for ln in text.splitlines())
            self.assertEqual(code.count("{"), code.count("}"), env)
            for ln in code.splitlines():
                ln = ln.strip()
                self.assertTrue(not ln or ln[-1] in "{};", (env, ln))
            self.assertIn(MOD.MARK, text.splitlines()[0])
            self.assertIn(MOD.MARK, steps.splitlines()[0])
            for ln in (text + steps).splitlines():
                self.assertEqual(ln, ln.rstrip(), "no spaces at a line's end")
                self.assertLessEqual(len(ln), 125, ln)

    def test_keycloak_lines(self):
        text, _ = blocks()
        b = part(text, "AAP, Keycloak's card")
        code = [ln for ln in b.splitlines() if not ln.startswith("#")]
        self.assertEqual(len(code), 1, "one line to add")
        self.assertTrue(code[0].startswith("proxy_set_header ssl-client-cert $ssl_client_escaped_cert;"))
        self.assertIn("ssl_verify_client optional;", b)
        self.assertIn("INSIDE the location that already passes requests on to Keycloak", b)
        text, _ = blocks(KEYCLOAK_VERIFY_HEADER="on")
        self.assertIn("proxy_set_header ssl-client-verify $ssl_client_verify;", part(text, "AAP, Keycloak's card"))

    def test_keycloak_server(self):
        text, steps = blocks(KEYCLOAK_NGINX="server", KEYCLOAK_UPSTREAM="http://keycloak:8080", **CA)
        b = part(text, "AAP, Keycloak")
        for line in ("server_name keycloak.yoursite.mil;", "ssl_client_certificate /etc/nginx/tls/dod-ca-chain.pem;",
                     "ssl_verify_client optional;", "ssl_verify_depth 3;", "proxy_pass http://keycloak:8080;",
                     "proxy_set_header ssl-client-cert $ssl_client_escaped_cert;", "proxy_set_header X-Forwarded-Proto https;",
                     "ssl_certificate     /etc/nginx/tls/keycloak-fullchain.pem;"):
            self.assertIn(line, b, line)
        self.assertNotRegex(b, r"ssl_verify_client\s+on;", "AAP calls Keycloak with no card")
        self.assertIn("keycloak-fullchain.pem and keycloak-key.pem", steps)
        self.assertNotIn("Keycloak's card: begin", text)

    def test_keycloak_off(self):
        text, _ = blocks(KEYCLOAK_NGINX="off")
        self.assertNotIn("ssl-client-cert", text)
        self.assertEqual(text.count(": begin ("), 1)

    def test_no_reports_without_a_folder(self):
        text, steps = blocks()
        self.assertNotIn("reports", text.lower())
        self.assertNotIn("Volume=", steps)
        self.assertNotIn("RESTARTS", steps)
        self.assertNotIn("report_web_url", steps)

    def test_reports_location(self):
        text, steps = blocks(**FOLDER)
        c = part(text, "site-automation reports")
        for line in ("location = /reports { return 301 /reports/; }", "location /reports/ {",
                     "alias /usr/share/nginx/site-reports/;", "index index.html;", "limit_except GET HEAD { deny all; }",
                     'add_header Cache-Control "no-cache";', "add_header X-Content-Type-Options nosniff;"):
            self.assertIn(line, c, line)
        self.assertIn("INSIDE STIG Manager's own server block", c)
        self.assertNotIn("server {", c, "it sits behind STIG Manager's own card check")
        self.assertNotIn("autoindex", c)
        self.assertNotIn("/srv/site-reports", text, "the host's folder is in the mount line, not in nginx's file")
        text, _ = blocks(REPORTS_PATH="/site-reports/", **FOLDER)
        self.assertIn("location /site-reports/ {", text)

    def test_the_mount_and_the_restart_are_said_plainly(self):
        _, steps = blocks(**FOLDER)
        self.assertIn("Volume=/srv/site-reports:/usr/share/nginx/site-reports:ro,z", steps)
        self.assertIn("- /srv/site-reports:/usr/share/nginx/site-reports:ro,z", steps)
        self.assertIn("THIS RESTARTS STIG MANAGER'S NGINX", steps)
        self.assertIn("sudo systemctl restart stigman-nginx.service", steps)
        self.assertIn("REPORTS_UPSTREAM", steps, "the way with no restart is named")
        self.assertIn("https://stigman.yoursite.mil/reports/", steps)
        self.assertIn("report_web_url: https://stigman.yoursite.mil/reports", steps)
        self.assertLess(steps.index("Volume="), steps.index("Paste the blocks"), "the folder is there before nginx is told of it")
        _, steps = blocks(NGINX_CONTAINER="web", REPORT_WEB_FOLDER="/data/reports", REPORTS_MOUNT="/srv/r")
        self.assertIn("Volume=/data/reports:/srv/r:ro,z", steps)
        self.assertIn("sudo podman exec web nginx -t", steps)

    def test_reports_server(self):
        text, steps = blocks(REPORTS="server", REPORTS_URL="https://reports.yoursite.mil", **dict(FOLDER, **CA))
        c = part(text, "site-automation reports")
        for line in ("server_name reports.yoursite.mil;", "ssl_client_certificate /etc/nginx/tls/dod-ca-chain.pem;",
                     "ssl_verify_client on;", "root /usr/share/nginx/site-reports;", "limit_except GET HEAD { deny all; }"):
            self.assertIn(line, c, line)
        self.assertIn("https://reports.yoursite.mil/", steps)
        self.assertIn("reports-fullchain.pem and reports-key.pem", steps)

    def test_reports_with_no_restart(self):
        text, steps = blocks(REPORTS_UPSTREAM="http://web01.yoursite.mil:8080")
        c = part(text, "site-automation reports")
        self.assertIn("proxy_pass http://web01.yoursite.mil:8080/;", c)
        self.assertIn("limit_except GET HEAD { deny all; }", c)
        self.assertNotIn("alias", c)
        self.assertNotIn("Volume=", steps)
        self.assertNotIn("RESTARTS", steps)
        self.assertNotIn("systemctl restart", steps)

    def test_the_order_of_the_steps(self):
        _, steps = blocks(**FOLDER)
        order = [steps.index(x) for x in (".before-aap", "Put the certificates", "Volume=", "Paste the blocks", "grep -c",
                                          "nginx -t\n", "nginx -s reload", "/api/gateway/v1/ping/")]
        self.assertEqual(order, sorted(order))
        self.assertEqual(re.findall(r"(?m)^(\d+)\. ", steps), [str(i) for i in range(1, 9)])
        self.assertIn("To take it all out again", steps)
        self.assertIn("then steps 6 and 7 again.", steps)


class WhatItWrites(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="aapngx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_it(self, *args, **env):
        e = dict(BASE, PATH=os.environ["PATH"], **env)
        e = {k: v for k, v in e.items() if v is not None}
        r = subprocess.run([sys.executable, SCRIPT] + list(args), capture_output=True, text=True, env=e, cwd=self.tmp,
                           stdin=subprocess.DEVNULL, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def tree(self):
        found = []
        for d, _, files in os.walk(self.tmp):
            found += [os.path.relpath(os.path.join(d, f), self.tmp) for f in files]
        return sorted(found)

    def test_two_files_in_out_and_nothing_else(self):
        rc, out, err = self.run_it(**FOLDER)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.tree(), ["out/aap-nginx-blocks.conf", "out/aap-nginx-steps.txt"])
        self.assertIn("Nothing else was touched.", out)
        text, steps = blocks(**FOLDER)
        self.assertEqual(read(os.path.join(self.tmp, "out", "aap-nginx-blocks.conf")), text)
        self.assertEqual(read(os.path.join(self.tmp, "out", "aap-nginx-steps.txt")), steps)
        rc, out, err = self.run_it(KEYCLOAK_NGINX="off")
        self.assertEqual(rc, 0, "its own files are written again: " + err)
        self.assertNotIn("reports", read(os.path.join(self.tmp, "out", "aap-nginx-blocks.conf")))

    def test_print_writes_nothing(self):
        rc, out, err = self.run_it("--print", **FOLDER)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.tree(), [])
        self.assertEqual(out, blocks(**FOLDER)[0].rstrip() + "\n")

    def test_never_where_nginx_lives(self):
        os.makedirs(os.path.join(self.tmp, "nginx", "conf.d"))
        os.symlink(os.path.join(self.tmp, "nginx"), os.path.join(self.tmp, "link"))
        for dest in ("/etc/nginx/conf.d", "/etc", "/etc/site-automation", os.path.join(self.tmp, "nginx", "conf.d"),
                     "nginx", "link"):
            rc, out, err = self.run_it("--out", dest)
            self.assertEqual(rc, 2, dest)
            self.assertIn("never writes where nginx's own files live", err)
        self.assertEqual(self.tree(), [])

    def test_a_file_it_did_not_write_is_left_alone(self):
        os.makedirs(os.path.join(self.tmp, "out"))
        mine = os.path.join(self.tmp, "out", "aap-nginx-blocks.conf")
        with open(mine, "w") as f:
            f.write("server { listen 443 ssl; }\n")
        rc, out, err = self.run_it()
        self.assertEqual(rc, 2, out + err)
        self.assertIn("this script did not write it", err)
        self.assertEqual(read(mine), "server { listen 443 ssl; }\n")
        self.assertEqual(self.tree(), ["out/aap-nginx-blocks.conf"])

    def test_settings_file_and_the_environment(self):
        p = os.path.join(self.tmp, "s.env")
        with open(p, "w") as f:
            f.write("# names\nAAP_URL=https://automation.yoursite.mil\nAAP_UPSTREAM=https://aap01.yoursite.mil:8443/\n"
                    "KEYCLOAK_URL=https://keycloak.yoursite.mil\nNGINX_LISTEN=8443 ssl\nKEYCLOAK_REALM=aap\nX509_IDENTITY=upn\n")
        rc, out, err = self.run_it("--settings", p, "--print", AAP_URL=None, AAP_UPSTREAM=None, KEYCLOAK_URL=None)
        self.assertEqual(rc, 0, err)
        self.assertIn("server_name automation.yoursite.mil;", out)
        self.assertIn("listen 8443 ssl;", out)
        self.assertIn("proxy_pass https://aap01.yoursite.mil:8443;", out)
        rc, out, err = self.run_it("--settings", p, "--print", AAP_URL="https://aap.yoursite.mil")
        self.assertIn("server_name aap.yoursite.mil;", out, "the environment wins over the file")
        with open(p, "a") as f:
            f.write("AAP_UPSTRAEM=x\n")
        rc, out, err = self.run_it("--settings", p, "--print")
        self.assertEqual(rc, 2)
        self.assertIn("line 8: not a setting: AAP_UPSTRAEM", err)

    def test_wrong_settings_stop_it(self):
        for env, said in (({"AAP_URL": None}, "Set AAP_URL"), ({"KEYCLOAK_URL": None}, "Set KEYCLOAK_URL"),
                          ({"AAP_UPSTREAM": None}, "Set AAP_UPSTREAM"),
                          ({"AAP_URL": "https://stigman.yoursite.mil/aap"}, "does not work under /something/"),
                          ({"AAP_UPSTREAM": "https://aap.yoursite.mil:8443"}, "nginx would call itself"),
                          ({"KEYCLOAK_NGINX": "yes"}, "lines, server or off"),
                          ({"KEYCLOAK_NGINX": "server"}, "needs KEYCLOAK_UPSTREAM"),
                          ({"KEYCLOAK_NGINX": "server", "KEYCLOAK_UPSTREAM": "http://keycloak:8080"}, "needs NGINX_CLIENT_CA"),
                          (dict(FOLDER, REPORTS_UPSTREAM="http://web01.yoursite.mil:8080"), "not both"),
                          ({"REPORT_WEB_FOLDER": "\\\\nas01\\reports"}, "does not belong"),
                          ({"REPORT_WEB_FOLDER": "srv/reports"}, "starts with /"),
                          (dict(FOLDER, REPORTS_PATH="/"), "REPORTS_PATH must be one folder name"),
                          (dict(FOLDER, REPORTS_PATH="/a/b/"), "REPORTS_PATH must be one folder name"),
                          (dict(FOLDER, REPORTS="server"), "needs REPORTS_URL"),
                          (dict(FOLDER, REPORTS_MOUNT="/etc/nginx"), "a folder of its own"),
                          (dict(FOLDER, REPORTS_MOUNT="/etc/nginx/tls/"), "a folder of its own")):
            rc, out, err = self.run_it(**env)
            self.assertEqual(rc, 2, (env, out, err))
            self.assertIn(said, err, env)
            self.assertEqual(self.tree(), [], env)
        for args in (("--apply",), ("--out",), ("extra",)):
            rc, out, err = self.run_it(*args)
            self.assertEqual(rc, 2, args)

    def test_a_setting_cannot_add_a_line_to_nginx(self):
        for k, v in (("AAP_UPSTREAM", "https://aap01.yoursite.mil; return 200 ok"),
                     ("AAP_URL", "https://aap.yoursite.mil;}"), ("NGINX_LISTEN", "443 ssl; server_name x"),
                     ("AAP_TLS_CERT", "a.pem;\nssl_verify_client off"), ("REPORTS_MOUNT", "/x; autoindex on"),
                     ("NGINX_TLS_DIR", "/etc/nginx/tls {"), ("REPORT_WEB_FOLDER", "/srv/$(id)"),
                     ("NGINX_CONTAINER", "nginx; rm -rf /"), ("REPORT_WEB_FOLDER", "/srv/my reports"),
                     ("REPORT_WEB_FOLDER", "/srv/`id`"), ("NGINX_CONF", "/etc/nginx/x.conf' ; id '")):
            rc, out, err = self.run_it("--print", **dict(FOLDER, **{k: v}))
            self.assertEqual(rc, 2, (k, v, out))
            self.assertEqual(out, "", k)


class TheScript(unittest.TestCase):

    def test_it_only_writes_text(self):
        """No podman, no nginx, no network, no command run: three imports, and one place that opens a file to write."""
        src = read(SCRIPT)
        self.assertEqual(sorted(set(re.findall(r"(?m)^(?:import|from) ([a-z_.]+)", src))), ["os", "re", "sys"])
        for word in ("os.system", "popen", "exec(", "eval(", "os.remove", "os.unlink", "os.rename", "shutil"):
            self.assertNotIn(word, src, word)
        self.assertEqual(len(re.findall(r"open\([^)]*\"w\"", src)), 1)
        self.assertTrue(src.startswith("#!/usr/bin/env python3\n"))
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertNotIn(":=", src)

    def test_help_names_every_setting(self):
        r = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for k in MOD.DEFAULTS:
            self.assertIn(k, r.stdout, k)
        self.assertNotIn("@SETTINGS@", r.stdout)

    def test_one_settings_file_for_the_three_scripts(self):
        realm = load(REALM, "aap_keycloak_realm")
        check = set(re.findall(r'setting ([A-Z_]+)', read(CHECK))) - {"NAME"}
        self.assertTrue({"AAP_URL", "KEYCLOAK_URL", "KEYCLOAK_REALM", "CA_FILE", "AAP_TOKEN_FILE"} <= check, check)
        every = set(MOD.DEFAULTS) | set(realm.DEFAULTS) | check
        self.assertEqual(set(MOD.DEFAULTS) | set(MOD.OTHER), every)
        self.assertEqual(set(realm.DEFAULTS) | set(realm.OTHER), every)
        s = MOD.read_settings(EXAMPLE, {})
        MOD.check_settings(s)
        self.assertEqual(s["NGINX_CONTAINER"], "stigman-nginx")
        self.assertEqual(s["KEYCLOAK_NGINX"], "lines")
        self.assertEqual(s["REPORT_WEB_FOLDER"], "", "the report website is asked for, not assumed")
        for mod in (MOD, realm):
            for line in read(EXAMPLE).splitlines():
                m = re.match(r"#([A-Z][A-Z0-9_]+)=(.*)", line)
                if m:
                    self.assertTrue(m.group(1) in mod.DEFAULTS or m.group(1) in mod.OTHER, m.group(1))


@unittest.skipUnless(shutil.which("nginx") and shutil.which("openssl"), "nginx and openssl are needed")
class NginxAcceptsThem(unittest.TestCase):
    """Each kind of block, put where its "Where:" line says in a small STIG Manager-like file, passes nginx -t."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aapngt")
        cls.tls = os.path.join(cls.tmp, "tls")
        os.makedirs(cls.tls)
        key, cert = os.path.join(cls.tls, "key.pem"), os.path.join(cls.tls, "cert.pem")
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                            "-days", "30", "-subj", "/CN=localhost"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        for name in ("aap", "keycloak", "reports", "stigman"):
            shutil.copy(cert, os.path.join(cls.tls, name + "-fullchain.pem"))
            shutil.copy(key, os.path.join(cls.tls, name + "-key.pem"))
        shutil.copy(cert, os.path.join(cls.tls, "ca.pem"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def nginx_t(self, **env):
        env = dict(env, NGINX_TLS_DIR=self.tls, NGINX_LISTEN="127.0.0.1:18743 ssl", AAP_UPSTREAM="https://127.0.0.1:18744",
                   REPORTS_MOUNT=os.path.join(self.tmp, "reports"))
        if "NGINX_CLIENT_CA" in env:
            env["NGINX_CLIENT_CA"] = os.path.join(self.tls, "ca.pem")
        text, _ = blocks(**env)
        inside_location = part(text, "AAP, Keycloak's card") if "Keycloak's card: begin" in text else ""
        reports = part(text, "site-automation reports") if "reports: begin" in text else ""
        in_server = reports if "server {" not in reports else ""
        outside = [part(text, "AAP")]
        if "AAP, Keycloak: begin" in text:
            outside.append(part(text, "AAP, Keycloak"))
        if reports and not in_server:
            outside.append(reports)
        conf = os.path.join(self.tmp, "nginx.conf")
        with open(conf, "w") as f:
            f.write("pid %s/nginx.pid;\nerror_log stderr;\nevents {}\nhttp {\naccess_log off;\n" % self.tmp
                    + "".join("%s_temp_path %s/t-%s;\n" % (k, self.tmp, k)
                              for k in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")) +
                    "server {\n listen 127.0.0.1:18743 ssl;\n server_name stigman.yoursite.mil;\n"
                    " ssl_certificate %s/stigman-fullchain.pem;\n ssl_certificate_key %s/stigman-key.pem;\n"
                    " ssl_client_certificate %s/ca.pem;\n ssl_verify_client optional;\n%s\n"
                    " location /kc/ {\n  proxy_pass http://127.0.0.1:18745/;\n%s\n }\n"
                    " location / { return 200 ok; }\n}\n%s\n}\n"
                    % (self.tls, self.tls, self.tls, in_server, inside_location, "\n".join(outside)))
        r = subprocess.run(["nginx", "-t", "-c", conf, "-p", self.tmp], capture_output=True, text=True)
        if r.returncode and re.search(r"Permission denied|unknown directive \"\w+_temp_path\"", r.stderr):
            self.skipTest("this machine's nginx cannot be tried as this user: " + r.stderr.strip().splitlines()[-1])
        self.assertEqual(r.returncode, 0, (env, r.stderr))

    def test_every_kind(self):
        for env in ({}, FOLDER, {"KEYCLOAK_VERIFY_HEADER": "on"}, {"KEYCLOAK_NGINX": "off"},
                    dict(KEYCLOAK_NGINX="server", KEYCLOAK_UPSTREAM="http://127.0.0.1:18745", **CA),
                    dict(FOLDER, REPORTS="server", REPORTS_URL="https://reports.yoursite.mil", **CA),
                    {"REPORTS_UPSTREAM": "http://127.0.0.1:18746"},
                    dict(REPORTS_UPSTREAM="http://127.0.0.1:18746", REPORTS="server", REPORTS_URL="https://reports.yoursite.mil",
                         **CA)):
            self.nginx_t(**env)


class TheDoc(unittest.TestCase):

    def test_part_2_is_the_script_and_the_paste(self):
        d = read(DOC)
        p2 = d[d.index("\n## 2. "):d.index("\n## 3. ")]
        self.assertIn("tools/aap-nginx-blocks.py --settings aap-keycloak.env", p2)
        for said in ("out/aap-nginx-blocks.conf", "out/aap-nginx-steps.txt", "restarts", "REPORTS_UPSTREAM",
                     "It must say `optional`", "nginx -t", "nginx -s reload", ".before-aap"):
            self.assertIn(said, p2, said)
        self.assertLess(p2.index(".before-aap"), p2.index("nginx -t"))
        self.assertLess(p2.index("nginx -t"), p2.index("nginx -s reload"))
        self.assertIn("never", p2.lower())

    def test_the_guide_shows_the_lines_the_script_writes(self):
        d = read(DOC)
        text, steps = blocks(**FOLDER)
        shown = d[d.index("What block A looks like"):]
        shown = shown[shown.index("```nginx\n") + 9:]
        shown = [ln.split("#")[0].strip() for ln in shown[:shown.index("```")].splitlines()]
        a = [ln.split("#")[0].strip() for ln in part(text, "AAP").splitlines()]
        self.assertGreater(len(shown), 10)
        for ln in shown:
            if ln and ln != "...":
                self.assertIn(ln, a, "the guide shows a line the script does not write: " + ln)
        for begin in ("AAP, Keycloak's card", "site-automation reports"):
            for ln in part(text, begin).splitlines():
                ln = ln.split("#")[0].strip()
                if ln.startswith(("proxy_set_header", "limit_except")):
                    self.assertIn(ln.split("{")[0].strip(), d, ln)
        self.assertIn("Volume=/srv/site-reports:/usr/share/nginx/site-reports:ro,z", d)
        self.assertIn("- /srv/site-reports:/usr/share/nginx/site-reports:ro,z", d)

    def test_every_setting_is_in_the_guide(self):
        d = read(DOC)
        section = d[d.index("### tools/aap-nginx-blocks.py"):d.index("### tools/aap-keycloak-realm.py")]
        for k in MOD.DEFAULTS:
            self.assertIn("`%s`" % k, section, k)


if __name__ == "__main__":
    unittest.main()
