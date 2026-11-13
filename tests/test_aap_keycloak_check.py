#!/usr/bin/env python3
"""Tests for AAP behind STIG Manager's nginx with the Keycloak login (1.51.0): tools/aap-keycloak-check.sh (bash -n,
ShellCheck when installed, read-only, and run for real against a fake nginx + gateway + Keycloak on localhost over
HTTPS: every check passes on a good setup, each mistake the guide's table names gives its own FAIL line, the realm
made for AAP is the one asked, and a realm that shows a password form to a caller with no card is a WARN) and
docs/AAP_KEYCLOAK.md saying the same as the script. python3 tests/test_aap_keycloak_check.py"""
import http.server
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO, "tools", "aap-keycloak-check.sh")
DOC = os.path.join(REPO, "docs", "AAP_KEYCLOAK.md")
TOKEN = "tok-3f9a7c21d4"
SLUG = "ansible_base-authentication-authenticator_plugins-oidc__keycloak"
ADMIN_PW = 'pw "q" \\ 9f2c&x=1'                                              # what a curl config must escape
ADMIN_TOKEN = "adm-71c0e5b8"
PLUGIN = "ansible_base.authentication.authenticator_plugins."
OIDC = {"name": "Keycloak AAP", "type": PLUGIN + "oidc", "enabled": True}


def federation(**config):
    """An LDAP federation as Keycloak's admin API lists it: every value a list of one text."""
    c = {"enabled": "true", "connectionUrl": "ldaps://dc01.corp.example.mil:636", "editMode": "READ_ONLY",
         "lastSync": str(int(time.time()) - 300), "changedSyncPeriod": "900", "fullSyncPeriod": "86400",
         "customUserSearchFilter": "(memberOf:1.2.840.113556.1.4.1941:=CN=AAP-Access,OU=Groups,DC=corp,DC=example,"
                                   "DC=mil)"}
    c.update(config)
    return {"id": "fed-1", "name": "directory", "providerId": "ldap",
            "providerType": "org.keycloak.storage.UserStorageProvider",
            "config": dict((k, [v]) for k, v in c.items() if v is not None)}


def read(p):
    with open(p) as f:
        return f.read()


class Fake(http.server.BaseHTTPRequestHandler):
    """nginx, the platform gateway and Keycloak in one: the mode says which mistake the setup has."""
    protocol_version = "HTTP/1.1"
    mode = "good"
    base = ""
    authenticators = [OIDC]                # AAP's login methods; a number is the status the gateway answers with
    federations = []                       # the realm's user federations
    people = [{"id": "u-1", "username": "1234567890123456@mil"}, {"id": "u-2", "username": "roe.jane"}]
    claims = {}                            # token kind -> the claims Keycloak's "evaluate" shows
    client = True
    admin_logins = []                      # the form of each admin login

    def _admin(self, path, query):
        """Keycloak's admin API of the realm aap, as far as the check reads it."""
        if self.headers.get("Authorization") != "Bearer " + ADMIN_TOKEN:
            return self._send(401, '{"error": "HTTP 401 Unauthorized"}')
        if path == "/components":
            if query.get("type") != ["org.keycloak.storage.UserStorageProvider"]:
                return self._send(200, "[]")
            return self._send(200, json.dumps(Fake.federations))
        if path == "/users/count":
            return self._send(200, str(len(Fake.people)))
        if path == "/clients":
            return self._send(200, json.dumps([{"id": "c-1", "clientId": "aap"}]
                                              if Fake.client and query.get("clientId") == ["aap"] else []))
        if path == "/users":
            if "username" in query:
                return self._send(200, json.dumps([u for u in Fake.people if [u["username"]] == query["username"]]))
            return self._send(200, json.dumps(Fake.people[:int(query.get("max", ["100"])[0])]))
        m = re.match(r"^/clients/c-1/evaluate-scopes/generate-example-(id-token|access-token|userinfo)$", path)
        if m and query.get("scope") == ["openid"] and query.get("userId", [""])[0] in [u["id"] for u in Fake.people]:
            claims = {"sub": query["userId"][0], "preferred_username": "x"}
            claims.update(Fake.claims.get(m.group(1), Fake.claims.get("all", {"groups": ["AAP-Operators", "AAP-Admins"]})))
            return self._send(200, json.dumps(claims))
        return self._send(404, '{"error": "not found"}')

    def do_POST(self):
        """The one request that is not a GET: the admin login. Nothing else may be posted."""
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        if self.path != "/realms/master/protocol/openid-connect/token":
            Fake.admin_logins.append({"path": self.path})
            return self._send(405, '{"error": "nothing is posted here"}')
        Fake.admin_logins.append(form)
        if Fake.mode == "admin_closed":
            return self._send(403, "<html>403 Forbidden</html>", "text/html")
        if form == {"grant_type": ["password"], "client_id": ["admin-cli"], "username": ["admin"],
                    "password": [ADMIN_PW]}:
            return self._send(200, json.dumps({"access_token": ADMIN_TOKEN, "expires_in": 60, "token_type": "Bearer"}))
        return self._send(401, '{"error": "invalid_grant", "error_description": "Invalid user credentials"}')

    def log_message(self, *a):
        pass

    def _send(self, code, body="", ctype="application/json", headers=()):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def do_GET(self):
        mode, base = Fake.mode, Fake.base
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        callback = "%s/api/gateway/social/complete/%s/" % (base, SLUG)
        if path.startswith("/admin/realms/aap/"):
            return self._admin(path[len("/admin/realms/aap"):], query)
        if path.startswith("/admin/"):
            return self._send(403, '{"error": "another realm"}')
        if path.startswith("/api/") and mode == "mtls":
            return self._send(400, "<html><head><title>400 No required SSL certificate was sent</title></head>"
                                   "<body><center>No required SSL certificate was sent</center></body></html>",
                              "text/html")
        if path.startswith("/realms/") and mode == "kc_mtls":
            return self._send(400, "<html><head><title>400 No required SSL certificate was sent</title></head>"
                                   "<body><center>No required SSL certificate was sent</center></body></html>",
                              "text/html")
        if path.startswith("/api/") and mode == "gateway_502":
            return self._send(502, "<html><body>502 Bad Gateway</body></html>", "text/html")
        if path.startswith("/api/") and mode == "other_vhost":
            return self._send(404, '{"error": "not found"}')
        if path == "/api/gateway/v1/ping/":
            return self._send(200, json.dumps({"version": "2.6", "pong": "2026-11-13 08:00:00"}))
        if path == "/api/gateway/v1/ui_auth/":
            ssos = [] if mode == "no_sso" else [
                {"name": "Keycloak", "login_url": "/api/gateway/social/login/%s/" % SLUG, "type": "oidc"}]
            return self._send(200, json.dumps({"show_login_form": True, "passwords": [], "ssos": ssos}, indent=2))
        if path == "/api/gateway/social/login/%s/" % SLUG:
            back = callback
            if mode == "http_redirect":
                back = callback.replace("https://", "http://", 1)
            elif mode == "other_host":
                back = "https://gateway.internal.example.mil/api/gateway/social/complete/%s/" % SLUG
            q = urllib.parse.urlencode({"client_id": "aap", "redirect_uri": back, "state": "s1",
                                        "response_type": "code", "scope": "openid profile email"})
            return self._send(302, "", headers=[("Location", "%s/realms/aap/protocol/openid-connect/auth?%s"
                                                 % (base, q))])
        if path == "/api/controller/v2/websocket/":
            if mode == "ws_login":
                return self._send(403, "")
            if mode != "no_upgrade" and self.headers.get("Upgrade", "").lower() == "websocket" \
                    and "upgrade" in self.headers.get("Connection", "").lower():
                self.send_response(101, "Switching Protocols")
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.end_headers()
                self.close_connection = True
                return None
            return self._send(400, "Bad Request", "text/plain")
        if path == "/api/gateway/v1/me/":
            if self.headers.get("Authorization") == "Bearer " + TOKEN:
                return self._send(200, '{"count": 1, "results": [{"username": "admin"}]}')
            return self._send(401, '{"detail": "Authentication credentials were not provided."}')
        if path == "/api/gateway/v1/authenticators/":
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self._send(401, '{"detail": "Authentication credentials were not provided."}')
            if isinstance(Fake.authenticators, int):
                return self._send(Fake.authenticators, '{"detail": "You do not have permission to perform this action."}')
            rows = [dict(m, id=n + 1) for n, m in enumerate(Fake.authenticators)]
            return self._send(200, json.dumps({"count": len(rows), "next": None, "previous": None, "results": rows}))
        if path == "/realms/aap/.well-known/openid-configuration":
            issuer = base + "/realms/aap"
            if mode == "bad_issuer":
                issuer = "https://keycloak-int.example.mil/realms/aap"
            return self._send(200, json.dumps({
                "issuer": issuer,
                "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
                "token_endpoint": issuer + "/protocol/openid-connect/token"}))
        if path == "/realms/aap/protocol/openid-connect/auth":
            if query.get("client_id") != ["aap"]:
                return self._send(400, "<html>We are sorry... Client not found.</html>", "text/html")
            if mode == "unregistered" or query.get("redirect_uri") != [callback]:
                return self._send(400, "<html>We are sorry... Invalid parameter: redirect_uri</html>", "text/html")
            if mode == "password_form":
                return self._send(200, '<html><form><input name="username"><input name="password" type="password">'
                                       '</form></html>', "text/html")
            return self._send(400, "<html>We are sorry... Invalid username or password.</html>", "text/html")
        return self._send(404, '{"error": "not found"}')


@unittest.skipUnless(shutil.which("curl") and shutil.which("openssl"), "curl and openssl are needed")
class RunsAgainstAFake(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aapkc")
        cls.cert, cls.key = os.path.join(cls.tmp, "cert.pem"), os.path.join(cls.tmp, "key.pem")
        r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", cls.key,
                            "-out", cls.cert, "-days", "400", "-subj", "/CN=localhost",
                            "-addext", "subjectAltName=DNS:localhost"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.httpd.handle_error = lambda *a: None                             # a client that hangs up is not news
        # the CAs of people's cards: nginx names them to a browser. Two here, and a third nginx does not ask for.
        cls.card_cas = os.path.join(cls.tmp, "card-cas.pem")
        cls.other_cas = os.path.join(cls.tmp, "other-cas.pem")
        pems = []
        for n, cn in enumerate(("Card Root CA 1", "Card ID CA 71", "Other Network CA 9")):
            pem = os.path.join(cls.tmp, "ca%d.pem" % n)
            r = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout",
                                os.path.join(cls.tmp, "ca%d.key" % n), "-out", pem, "-days", "400",
                                "-subj", "/C=US/O=Example/OU=PKI/CN=" + cn,
                                "-addext", "basicConstraints=critical,CA:TRUE"], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            pems.append(read(pem))
        with open(cls.card_cas, "w") as f:
            f.write(pems[0] + pems[1])
        with open(cls.other_cas, "w") as f:
            f.write(pems[0] + pems[2])
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.cert, cls.key)
        cls.httpd.socket = ctx.wrap_socket(cls.httpd.socket, server_side=True)
        cls.port = cls.httpd.server_address[1]
        Fake.base = cls.base = "https://localhost:%d" % cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.token_file = os.path.join(cls.tmp, "token")
        with open(cls.token_file, "w") as f:
            f.write(TOKEN + "\n")
        os.chmod(cls.token_file, 0o600)
        # nginx names the card CAs in the TLS handshake. Python's ssl cannot, so "openssl s_server" does it, and a
        # stand-in openssl sends the one s_client call there. s_client and the script's reading of it are the real ones.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.card_port = sock.getsockname()[1]
        cls.asks_for_a_card = subprocess.Popen(
            ["openssl", "s_server", "-accept", "127.0.0.1:%d" % cls.card_port, "-cert", cls.cert, "-key", cls.key,
             "-CAfile", cls.card_cas, "-verify", "2", "-www"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.card_port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        cls.cards_bin = os.path.join(cls.tmp, "cards-bin")
        os.makedirs(cls.cards_bin)
        with open(os.path.join(cls.cards_bin, "openssl"), "w") as f:
            f.write('#!/usr/bin/env bash\nargs=()\nfor a in "$@"; do\n  [ "$a" = "localhost:%d" ] && a="127.0.0.1:%d"\n'
                    '  args+=("$a")\ndone\nexec "%s" "${args[@]}"\n' % (cls.port, cls.card_port, shutil.which("openssl")))
        os.chmod(os.path.join(cls.cards_bin, "openssl"), 0o755)
        cls.admin_file = os.path.join(cls.tmp, "admin.pw")
        with open(cls.admin_file, "w") as f:
            f.write(ADMIN_PW + "\n")
        os.chmod(cls.admin_file, 0o600)

    @classmethod
    def tearDownClass(cls):
        cls.asks_for_a_card.kill()
        cls.asks_for_a_card.wait()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Fake.authenticators, Fake.federations, Fake.claims, Fake.client = [OIDC], [federation()], {}, True
        Fake.admin_logins = []

    def run_check(self, mode="good", path=None, args=(), cards=True, **env):
        Fake.mode = mode
        path = path or os.environ["PATH"]
        if cards:                                                            # nginx asks for a card, or not
            path = path.replace(os.environ["PATH"], self.cards_bin + os.pathsep + os.environ["PATH"])
        e = {"PATH": path, "AAP_URL": self.base, "KEYCLOAK_URL": self.base,
             "CA_FILE": self.cert, "CHECK_TIMEOUT": "5"}
        e.update(env)
        e = {k: v for k, v in e.items() if v is not None}
        r = subprocess.run(["bash", SCRIPT] + list(args), capture_output=True, text=True, env=e, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def lines(self, out, word):
        return [ln for ln in out.splitlines() if ln.startswith(word)]

    def everything(self, **env):
        """Every check has what it needs: the token, the admin password, the card CAs, the directory."""
        e = dict(AAP_TOKEN_FILE=self.token_file, KEYCLOAK_ADMIN_PASSWORD_FILE=self.admin_file,
                 CARD_CA_FILE=self.card_cas, LDAP_URL="ldaps://dc01.corp.example.mil:636")
        e.update(env)
        return e

    def stand_ins(self, *names):
        """Stand-ins that write their arguments down and then run the real program: (PATH, the log's path)."""
        bindir = tempfile.mkdtemp(dir=self.tmp)
        log = os.path.join(bindir, "argv.log")
        for name in names:
            with open(os.path.join(bindir, name), "w") as f:
                f.write('#!/usr/bin/env bash\nprintf \'%%s %%s\\n\' "%s" "$*" >>"%s"\nexec "%s" "$@"\n'
                        % (name, log, shutil.which(name, path=self.cards_bin + os.pathsep + os.environ["PATH"])))
            os.chmod(os.path.join(bindir, name), 0o755)
        return bindir + os.pathsep + os.environ["PATH"], log

    def test_good_setup_passes_everything(self):
        rc, out, err = self.run_check(**self.everything())
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.lines(out, "FAIL"), [], out)
        self.assertEqual(self.lines(out, "SKIP"), [], out)
        self.assertEqual(self.lines(out, "WARN"), [], out)
        for check in ("DNS", "TLS", "Gateway", "Keycloak", "Redirect", "Card", "Websocket", "Token"):
            self.assertTrue([ln for ln in self.lines(out, "PASS") if ln.split()[1] == check], check + "\n" + out)
        for check in ("Card CAs", "AAP login", "LDAP sync", "Groups"):
            self.assertTrue([ln for ln in self.lines(out, "PASS") if ln[6:].startswith(check + " ")], check + "\n" + out)
        self.assertIn("it comes back to %s/api/gateway/social/complete/%s/" % (self.base, SLUG), out)
        self.assertIn("HTTP 101", out)
        self.assertIn("Nothing failed.", out)

    # --- AAP's own login methods (the token reads them)

    def test_an_ldap_login_in_aap_next_to_keycloak_is_a_warning(self):
        """What was found at the site: the LDAP authenticator made before Keycloak, still enabled."""
        Fake.authenticators = [OIDC, {"name": "AD LDAP", "type": PLUGIN + "ldap", "enabled": True}]
        rc, out, err = self.run_check(AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out + err)
        self.assertIn('WARN  AAP login  the LDAP authenticator "AD LDAP" is enabled next to the Keycloak one:\n'
                      '                 a password login that bypasses the card is on.\n', out)
        self.assertIn("Authentication Methods > AD LDAP > untick Enabled (guide, step 5.9).", out)
        self.assertNotRegex(out, r"(?m)^PASS  AAP login ")

    def test_an_ldap_login_that_is_disabled_passes(self):
        Fake.authenticators = [OIDC, {"name": "AD LDAP", "type": PLUGIN + "ldap", "enabled": False},
                               {"name": "Local", "type": PLUGIN + "local", "enabled": True}]
        rc, out, err = self.run_check(AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out + err)
        self.assertRegex(out, r"(?m)^PASS  AAP login  no LDAP, RADIUS or TACACS\+ method is enabled in AAP "
                              r"\(1 single sign-on enabled\)$")
        self.assertNotRegex(out, r"(?m)^WARN  AAP login ")

    def test_radius_and_tacacs_are_password_logins_too(self):
        Fake.authenticators = [OIDC, {"name": "r1", "type": PLUGIN + "radius", "enabled": True},
                               {"name": "t1", "type": PLUGIN + "tacacs", "enabled": True}]
        rc, out, _ = self.run_check(AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out)
        self.assertIn('the RADIUS authenticator "r1" is enabled', out)
        self.assertIn('the TACACS authenticator "t1" is enabled', out)

    def test_a_token_that_may_not_read_the_methods_is_a_skip(self):
        Fake.authenticators = 403
        rc, out, _ = self.run_check(AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^SKIP  AAP login  HTTP 403: this token may not read the authentication methods")
        Fake.authenticators = 404
        rc, out, _ = self.run_check(AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^SKIP  AAP login  HTTP 404 from https://localhost:\d+/api/gateway/v1/authenticators/")

    # --- the admin's checks: the people from the directory, and the groups claim

    def test_no_admin_password_file_skips_both(self):
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP  LDAP sync  no KEYCLOAK_ADMIN_PASSWORD_FILE given (guide, step 6.5)\n"
                      "SKIP  Groups     no KEYCLOAK_ADMIN_PASSWORD_FILE given (guide, step 6.5)\n", out)
        self.assertEqual(Fake.admin_logins, [])

    def test_the_admin_password_is_never_on_a_command_line(self):
        """Quotes, a backslash, & and = in it: it reaches Keycloak whole, on curl's standard input."""
        path, log = self.stand_ins("curl")
        rc, out, err = self.run_check(path=path, **self.everything())
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(Fake.admin_logins), 1)
        self.assertEqual(Fake.admin_logins[0]["password"], [ADMIN_PW])
        argv = read(log)
        self.assertIn("/realms/master/protocol/openid-connect/token", argv)
        for secret in (ADMIN_PW, "9f2c", ADMIN_TOKEN, TOKEN):
            self.assertNotIn(secret, argv)
            self.assertNotIn(secret, out + err)

    def test_the_admin_login_is_the_only_post(self):
        rc, out, err = self.run_check(**self.everything())
        self.assertEqual(rc, 0, out + err)
        self.assertEqual([sorted(f) for f in Fake.admin_logins], [["client_id", "grant_type", "password", "username"]])

    def test_a_wrong_admin_password(self):
        bad = os.path.join(self.tmp, "bad.pw")
        with open(bad, "w") as f:
            f.write("not-it-41\n")
        rc, out, err = self.run_check(KEYCLOAK_ADMIN_PASSWORD_FILE=bad)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  LDAP sync  Keycloak did not accept the admin account admin (realm master)\n"
                      "SKIP  Groups     no admin login\n", out)
        self.assertNotIn("not-it-41", out + err)

    def test_the_admin_address_is_closed(self):
        rc, out, _ = self.run_check("admin_closed", KEYCLOAK_ADMIN_PASSWORD_FILE=self.admin_file)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  LDAP sync  no admin login at https://localhost:\d+ \(HTTP 403\)$")
        self.assertIn("KEYCLOAK_ADMIN_URL=http://localhost:8080 (guide, step 4.2).", out)

    def admin(self, **env):
        rc, out, err = self.run_check(KEYCLOAK_ADMIN_PASSWORD_FILE=self.admin_file, **env)
        return rc, [ln for ln in out.splitlines() if ln[6:].startswith(("LDAP sync", "Groups")) or ln.startswith("    ")], out

    def test_ldap_sync_says_what_it_found(self):
        rc, lines, out = self.admin(LDAP_URL="ldaps://dc01.corp.example.mil:636")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^PASS  LDAP sync  the federation directory \(ldaps://dc01\.corp\.example\.mil:636, "
                              r"READ_ONLY\) is on; the realm holds 2 people$")
        self.assertRegex(out, r"(?m)^PASS  LDAP sync  the last sync finished \d{4}-\d\d-\d\d \d\d:\d\d UTC, 5 minutes ago$")
        self.assertEqual([ln for ln in lines if ln.startswith(("WARN", "FAIL", "SKIP"))], [], out)

    def test_no_federation(self):
        Fake.federations = [{"id": "k", "name": "kerberos", "providerId": "kerberos", "config": {}}]
        rc, lines, out = self.admin(LDAP_URL="ldaps://dc01.corp.example.mil:636")
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  LDAP sync  realm aap has no LDAP federation, and LDAP_URL is set\n", out)
        self.assertIn("(guide, step 4.8).", out)
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP  LDAP sync  realm aap has no LDAP federation (no LDAP_URL either)\n", out)
        self.assertRegex(out, r"(?m)^PASS  Groups ")

    def test_a_federation_that_is_off_fails(self):
        Fake.federations = [federation(enabled="false")]
        rc, lines, out = self.admin()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  LDAP sync  the federation directory (ldaps://dc01.corp.example.mil:636) is switched off\n",
                      out)

    def test_a_federation_that_may_write_or_lets_everybody_in_is_a_warning(self):
        Fake.federations = [federation(editMode="WRITABLE", customUserSearchFilter=None)]
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN  LDAP sync  the federation directory is WRITABLE, not READ_ONLY: Keycloak may write to the "
                      "directory\n", out)
        self.assertIn("WARN  LDAP sync  the federation directory has no filter: every person under its users DN can log "
                      "in\n                 LDAP_GROUP_DN names the one group whose members may (guide, step 4.8).\n", out)

    def test_the_sync_times(self):
        Fake.federations = [federation(lastSync=None)]
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN  LDAP sync  no sync has finished yet. A member is still taken in at the first card login\n",
                      out)
        Fake.federations = [federation(lastSync=str(int(time.time()) - 3 * 3600))]
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^WARN  LDAP sync  the last sync finished .* UTC, 3 hours ago: the timed one "
                              r"\(every 900 s\) is late$")
        self.assertIn("User federation > directory > Action > Sync changed users", out)
        Fake.federations = [federation(changedSyncPeriod="-1", fullSyncPeriod="86400",
                                       lastSync=str(int(time.time()) - 3 * 3600))]
        rc, lines, out = self.admin()
        self.assertRegex(out, r"(?m)^PASS  LDAP sync  the last sync finished .* UTC, 3 hours ago$")
        Fake.federations = [federation(changedSyncPeriod="-1", fullSyncPeriod="-1")]
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN  LDAP sync  no timed sync is set (LDAP_SYNC_CHANGED_SECONDS, LDAP_SYNC_FULL_SECONDS)\n", out)
        self.assertIn("is refused at the next card login anyway", out)

    def test_a_token_without_the_groups_claim_fails(self):
        """The hand-made realm with no Group Membership mapper on the client: AAP's maps would get nothing."""
        Fake.claims = {"all": {}}
        rc, lines, out = self.admin()
        self.assertEqual(rc, 1, out)
        self.assertIn('FAIL  Groups     a token of 1234567890123456@mil has no claim "groups" (id-token, access-token, '
                      "userinfo): AAP's maps get nothing\n", out)
        self.assertIn("Group Membership mapper: claim groups, full path off,", out)
        self.assertIn("(guide, step 4.5).", out)
        Fake.claims = {"access-token": {}}                                    # the mapper leaves one token out
        rc, lines, out = self.admin()
        self.assertEqual(rc, 1, out)
        self.assertIn('has no claim "groups" (access-token): AAP', out)

    def test_the_groups_claim(self):
        rc, lines, out = self.admin()
        self.assertEqual(rc, 0, out)
        self.assertIn('PASS  Groups     the tokens of 1234567890123456@mil carry the claim "groups" (groups: 2)\n', out)
        Fake.claims = {"all": {"groups": []}}
        rc, lines, out = self.admin(CHECK_USER="roe.jane")
        self.assertEqual(rc, 0, out)
        self.assertIn('WARN  Groups     the claim "groups" is in the tokens, and empty for roe.jane (in no group)\n', out)
        self.assertIn("Another person: CHECK_USER.", out)
        Fake.claims = {"all": {"memberships": ["AAP-Admins"]}}
        rc, lines, out = self.admin(AAP_GROUPS_CLAIM="memberships")
        self.assertEqual(rc, 0, out)
        self.assertIn('carry the claim "memberships" (groups: 1)\n', out)

    def test_groups_with_nobody_or_no_client(self):
        rc, lines, out = self.admin(CHECK_USER="nobody.here")
        self.assertEqual(rc, 0, out)
        self.assertIn("SKIP  Groups     the realm has no person nobody.here (CHECK_USER)\n", out)
        Fake.client = False
        rc, lines, out = self.admin()
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  Groups     realm aap has no client aap\n", out)

    # --- the CAs of people's cards

    def test_card_cas_are_the_ones_of_the_file_and_the_line_names_it(self):
        rc, out, err = self.run_check(CARD_CA_FILE=self.card_cas)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("PASS  Card CAs   nginx on localhost takes a card from the 2 CAs of CARD_CA_FILE=%s\n"
                      % self.card_cas, out)
        self.assertIn("cards: %s\n" % self.card_cas, out)

    def test_card_cas_of_another_network(self):
        """The file of another network's PKI held against this nginx: both sides of the difference are named."""
        rc, out, err = self.run_check(CARD_CA_FILE=self.other_cas)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("WARN  Card CAs   nginx on localhost and CARD_CA_FILE=%s do not name the same CAs\n"
                      % self.other_cas, out)
        self.assertRegex(out, r"(?m)^ +in the file, not asked by nginx: .*CN ?= ?Other Network CA 9$")
        self.assertRegex(out, r"(?m)^ +asked by nginx, not in the file: .*CN ?= ?Card ID CA 71$")
        self.assertNotIn("Card Root CA 1", out)
        self.assertIn("(guide, step 4.1). Another network: another chain.", out)

    def test_card_cas_without_a_file_are_printed(self):
        rc, out, err = self.run_check()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("PASS  Card CAs   nginx on localhost takes a card from 2 CAs. No CARD_CA_FILE given: not compared\n",
                      out)
        self.assertRegex(out, r"(?m)^ +.*CN ?= ?Card Root CA 1$")
        self.assertIn("cards: none given (CARD_CA_FILE)\n", out)

    def test_nginx_asks_for_no_card(self):
        rc, out, err = self.run_check(cards=False, CARD_CA_FILE=self.card_cas)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("WARN  Card CAs   nginx on localhost names no CA for cards (CARD_CA_FILE: %s)\n" % self.card_cas,
                      out)
        self.assertIn("Guide, step 2.6.", out)

    def test_a_card_ca_file_with_no_certificate(self):
        empty = os.path.join(self.tmp, "empty.pem")
        with open(empty, "w") as f:
            f.write("not a certificate\n")
        rc, out, _ = self.run_check(CARD_CA_FILE=empty)
        self.assertEqual(rc, 1, out)
        self.assertIn("FAIL  Card CAs   CARD_CA_FILE=%s holds no certificate (PEM)\n" % empty, out)

    # --- a network with no way out

    def test_no_proxy_and_no_name_beyond_the_given_hosts(self):
        """An enclave: the proxy variables point nowhere and the only names are the ones in the settings. Every
        check still passes, and every curl, openssl and getent call names the given host and nothing else."""
        path, log = self.stand_ins("curl", "openssl", "getent")
        nowhere = "http://192.0.2.1:9"
        rc, out, err = self.run_check(path=path, **self.everything(
            http_proxy=nowhere, https_proxy=nowhere, HTTP_PROXY=nowhere, HTTPS_PROXY=nowhere, ALL_PROXY=nowhere,
            all_proxy=nowhere, no_proxy="", NO_PROXY=""))
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.lines(out, "FAIL") + self.lines(out, "SKIP") + self.lines(out, "WARN"), [], out)
        calls = read(log).splitlines()
        curls = [c for c in calls if c.startswith("curl ")]
        self.assertGreater(len(curls), 10)
        for c in curls:
            self.assertIn(" --noproxy * ", c, c)
            urls = re.findall(r"\b[a-z]+://\S+", c)
            self.assertTrue(urls, c)
            for u in urls:
                self.assertTrue(u == self.base or u.startswith(self.base + "/"), c)
        connects = [c for c in calls if c.startswith("openssl ") and "-connect" in c]
        self.assertEqual(len(connects), 2, calls)                           # the certificate's end date, the card CAs
        for c in connects:
            self.assertIn("-connect localhost:%d -servername localhost" % self.port, c)
        for c in calls:
            if c.startswith("openssl ") and "-connect" not in c:
                self.assertNotRegex(c, r"://|-url|-host", c)                  # the rest only read the CA file
        self.assertEqual(set(c for c in calls if c.startswith("getent ")), {"getent hosts localhost"})
        self.assertEqual(set(c.split()[0] for c in calls), {"curl", "openssl", "getent"})

    def test_no_token_is_a_skip_not_a_fail(self):
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^SKIP  Token ")

    def test_token_never_on_a_command_line(self):
        """Every curl call's arguments are written down by a stand-in curl: the token is in none of them."""
        path, log = self.stand_ins("curl")
        rc, out, err = self.run_check(path=path, AAP_TOKEN_FILE=self.token_file)
        self.assertEqual(rc, 0, out + err)
        self.assertRegex(out, r"(?m)^PASS  Token ")
        argv = read(log)
        self.assertIn("/api/gateway/v1/me/", argv)
        self.assertNotIn(TOKEN, argv)
        self.assertNotIn(TOKEN, out + err)

    def test_wrong_token(self):
        bad = os.path.join(self.tmp, "badtoken")
        with open(bad, "w") as f:
            f.write("not-the-token\n")
        rc, out, _ = self.run_check(AAP_TOKEN_FILE=bad)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Token +HTTP 401")

    def test_mtls_on_the_aap_block(self):
        rc, out, _ = self.run_check("mtls")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Gateway +nginx asks for a client certificate")
        self.assertIn("ssl_verify_client off;", out)
        self.assertRegex(out, r"(?m)^SKIP  Redirect ")
        self.assertRegex(out, r"(?m)^SKIP  Websocket ")

    def test_nginx_cannot_reach_the_gateway(self):
        rc, out, _ = self.run_check("gateway_502")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Gateway +HTTP 502: nginx answers but cannot reach the gateway")

    def test_another_server_block_answers(self):
        rc, out, _ = self.run_check("other_vhost")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Gateway +HTTP 404: another server block answered")

    def test_redirect_over_http(self):
        rc, out, _ = self.run_check("http_redirect")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Redirect +it comes back over http: http://localhost")
        self.assertIn("X-Forwarded-Proto", out)

    def test_redirect_to_the_inner_name(self):
        rc, out, _ = self.run_check("other_host")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Redirect +it comes back to another name: https://gateway\.internal")
        self.assertIn("proxy_set_header Host $http_host;", out)

    def test_redirect_uri_not_in_keycloak(self):
        rc, out, _ = self.run_check("unregistered")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^PASS  Redirect +it comes back to https://localhost")
        self.assertRegex(out, r"(?m)^FAIL  Redirect +Keycloak does not know this redirect URI for client aap")
        self.assertIn("Valid redirect URIs", out)

    def test_the_card_login_is_on(self):
        """A caller with no card is refused by the realm: its browser flow has the card step and no password form."""
        rc, out, _ = self.run_check()
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^PASS  Card +realm aap refuses a caller with no card, and shows no password form")
        self.assertRegex(out, r"(?m)^PASS  Redirect +Keycloak accepts the client and the redirect URI")

    def test_a_password_form_is_a_warning(self):
        rc, out, _ = self.run_check("password_form")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^WARN  Card +realm aap shows a user name and password form to a caller with no card")
        self.assertIn("tools/aap-keycloak-realm.py", out)
        self.assertIn("guide, step 4.5", out)

    def test_the_settings_file(self):
        """The file the other two scripts read: read as text (never run), and the environment wins over it."""
        p = os.path.join(self.tmp, "s.env")
        mark = os.path.join(self.tmp, "was-run")
        with open(p, "w") as f:
            f.write("# names\nAAP_URL=%s\nKEYCLOAK_URL='%s'\nKEYCLOAK_REALM=other\nKEYCLOAK_REALM=aap\n"
                    "CA_FILE=\"%s\"\nNGINX_CONTAINER=stigman-nginx\nX509_REGEX=$(touch %s)\nAAP_GROUPS=`touch %s`\n"
                    % (self.base, self.base, self.cert, mark, mark))
        rc, out, err = self.run_check(args=("--settings", p), AAP_URL=None, KEYCLOAK_URL=None, CA_FILE=None)
        self.assertEqual(rc, 0, out + err)
        self.assertRegex(out, r"(?m)^PASS  Card +realm aap ")
        self.assertFalse(os.path.exists(mark), "the file is read, not run")
        rc, out, err = self.run_check(args=("--settings", p), AAP_URL=None, KEYCLOAK_URL=None, CA_FILE=None,
                                      KEYCLOAK_REALM="other")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Keycloak +HTTP 404 from .*/realms/other/")
        for args in (("--settings", os.path.join(self.tmp, "none.env")), ("--settings",), ("--apply",), ("extra",)):
            rc, out, err = self.run_check(args=args)
            self.assertEqual(rc, 2, args)
            self.assertEqual(out, "", args)

    def test_no_single_sign_on_yet(self):
        rc, out, _ = self.run_check("no_sso")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Redirect +AAP offers no single sign-on login yet")

    def test_client_id_differs_is_a_warning(self):
        rc, out, _ = self.run_check(KEYCLOAK_CLIENT_ID="automation")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^WARN  Redirect +the client is aap, you said automation")

    def test_upgrade_headers_dropped(self):
        rc, out, _ = self.run_check("no_upgrade")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Websocket +HTTP 400, not 101")
        self.assertIn("proxy_set_header Upgrade $http_upgrade;", out)

    def test_websocket_behind_a_login_is_a_warning(self):
        rc, out, _ = self.run_check("ws_login")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"(?m)^WARN  Websocket +HTTP 403")

    def test_keycloak_names_itself_differently(self):
        rc, out, _ = self.run_check("bad_issuer")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Keycloak +the issuer is https://keycloak-int\.example\.mil/realms/aap")

    def test_keycloak_asks_callers_for_a_card(self):
        """AAP fetches the token itself, with no card: a Keycloak address that demands one ends every login."""
        rc, out, _ = self.run_check("kc_mtls")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Keycloak +Keycloak's address asks every caller for a client certificate")
        self.assertIn("guide, step 2.6", out)

    def test_wrong_realm(self):
        rc, out, _ = self.run_check(KEYCLOAK_REALM="other")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  Keycloak +HTTP 404")

    def test_certificate_not_trusted(self):
        rc, out, _ = self.run_check(CA_FILE=None)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  TLS +localhost: the certificate is not trusted or not for this name")

    def test_certificate_for_another_name(self):
        rc, out, _ = self.run_check(AAP_URL="https://127.0.0.1:%d" % self.port)
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  TLS +127\.0\.0\.1: the certificate is not trusted or not for this name")

    def test_name_does_not_resolve(self):
        rc, out, _ = self.run_check(AAP_URL="https://aap.nowhere.invalid")
        self.assertEqual(rc, 1, out)
        self.assertRegex(out, r"(?m)^FAIL  DNS +aap\.nowhere\.invalid does not resolve")

    def test_settings_are_checked_first(self):
        for env, said in (({"AAP_URL": None}, "Set AAP_URL"),
                          ({"KEYCLOAK_URL": None}, "Set KEYCLOAK_URL"),
                          ({"AAP_URL": "http://localhost"}, "must start with https://"),
                          ({"AAP_URL": self.base + "/aap"}, "must not have a path"),
                          ({"CA_FILE": "/nonexistent/ca.pem"}, "cannot be read"),
                          ({"AAP_TOKEN_FILE": "/nonexistent/token"}, "cannot be read")):
            rc, out, err = self.run_check(**env)
            self.assertEqual(rc, 2, (env, out, err))
            self.assertIn(said, err, env)
            self.assertEqual(out, "", env)


class TheScript(unittest.TestCase):

    def test_bash_n(self):
        r = subprocess.run(["bash", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_shellcheck(self):
        sc = shutil.which("shellcheck") or shutil.which("shellcheck", path=os.path.dirname(sys.executable))
        if not sc:
            self.skipTest("shellcheck is not installed (pip install shellcheck-py)")
        r = subprocess.run([sc, "-S", "warning", SCRIPT], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_strict_and_executable(self):
        s = read(SCRIPT)
        self.assertTrue(s.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("set -euo pipefail", s)
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_help(self):
        r = subprocess.run(["bash", SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("AAP_TOKEN_FILE", r.stdout)
        self.assertNotIn("set -euo", r.stdout)

    def test_it_only_asks(self):
        """No command that changes anything: no podman, nginx, systemctl or sudo, no file written, GET only (and
        one admin login)."""
        code = "\n".join(ln for ln in read(SCRIPT).splitlines() if not ln.lstrip().startswith("#"))
        for word in ("podman", "systemctl", "sudo", "nginx", "mktemp", "tee", "rm", "mv", "cp", "chmod", "dd"):
            self.assertFalse(re.search(r"(?m)(^|[|;&(]|\$\()\s*%s\s" % word, code), word)
        login = "--data grant_type=password --data client_id=admin-cli"
        self.assertEqual(code.count(login), 1, "the admin login, which changes nothing, is the one request with a body")
        self.assertFalse(re.search(r"--request|--data|--form|--upload-file|-X\s", code.replace(login, "")),
                         "everything else is a GET")
        targets = re.findall(r">>?([^\s&;)>]+)", code)                        # "A > B" in a message is not one
        self.assertTrue(targets)
        for target in targets:
            self.assertEqual(target, "/dev/null", target)
        self.assertFalse(re.search(r"--insecure|\s-k\s", code), "a bad certificate is a finding")


class TheDoc(unittest.TestCase):

    def test_points_at_the_script(self):
        d = read(DOC)
        self.assertIn("tools/aap-keycloak-check.sh", d)
        for name in ("AAP_URL", "KEYCLOAK_URL", "KEYCLOAK_REALM", "KEYCLOAK_CLIENT_ID", "CA_FILE", "AAP_TOKEN_FILE"):
            self.assertIn(name, d, name)

    def test_the_steps_the_script_names_exist(self):
        """A FAIL line sends the reader to 'guide, step N.M': that step is in the guide."""
        d = read(DOC)
        for part, step in set(re.findall(r"guide, step (\d)(?:\.(\d+))?", read(SCRIPT))):
            self.assertRegex(d, r"(?m)^## %s\. " % part, part)
            if step:
                section = d[re.search(r"(?m)^## %s\. " % part, d).start():]
                section = section[:section.index("\n## ", 1)] if "\n## " in section[1:] else section
                self.assertRegex(section, r"(?m)^%s\. " % step, "%s.%s" % (part, step))

    def test_the_seven_parts(self):
        d = read(DOC)
        for n, word in ((1, "Check first"), (2, "nginx"), (3, "AAP"), (4, "Keycloak"), (5, "AAP"), (6, "Test"),
                        (7, "Roll back")):
            self.assertRegex(d, r"(?m)^## %d\. .*%s" % (n, word), word)

    def test_nginx_block_has_what_the_checks_ask_for(self):
        """The lines a FAIL's hint asks for are in the guide's block A. (The whole block, as tools/aap-nginx-blocks.py
        writes it: tests/test_aap_nginx_blocks.py.)"""
        d = read(DOC)
        block = d[d.index("What block A looks like"):]
        block = block[block.index("```nginx"):]
        block = block[:block.index("```", 8)]
        for line in ("server_name aap.yoursite.mil;", "ssl_verify_client off;", "proxy_http_version 1.1;",
                     "proxy_set_header Host $http_host;", "proxy_set_header X-Forwarded-Proto https;",
                     "proxy_set_header Upgrade $http_upgrade;", "proxy_set_header Connection $connection_upgrade;",
                     "client_max_body_size"):
            self.assertIn(line, block, line)
        hints = re.findall(r'hint "(proxy_set_header [^;]+;)', read(SCRIPT))
        self.assertGreaterEqual(len(hints), 3)
        for line in hints:
            self.assertIn(line.replace("\\$", "$"), block, line)

    def test_nginx_is_tested_before_the_reload_and_a_copy_is_kept(self):
        d = read(DOC)
        part = d[d.index("\n## 2. "):d.index("\n## 3. ")]
        self.assertLess(part.index(".before-aap"), part.index("nginx -t"))
        self.assertLess(part.index("nginx -t"), part.index("nginx -s reload"))

    def test_sources_and_what_is_not_proven(self):
        d = read(DOC)
        self.assertIn("## Where each step comes from", d)
        self.assertIn("## Not in Red Hat's documents", d)
        tried = d[d.index("\n## What was tried"):]
        self.assertIn("**Not tried:**", tried)
        for said in ("Keycloak **26.0.6**", "a real AAP", "a real CAC"):
            self.assertIn(said, tried, said)

    def test_table_names_each_fail_of_the_script(self):
        d = read(DOC)
        t = d[d.index("### When it fails"):]
        for said in ("No required SSL certificate was sent", "Invalid parameter: redirect_uri", "http://",
                     "CSRF", "502", "Audience", "Invalid username or password"):
            self.assertIn(said, t, said)

    def test_placeholders_only(self):
        d = read(DOC)
        hosts = set(re.findall(r"https?://([A-Za-z0-9.-]+)", d))
        for h in hosts:
            self.assertTrue(h.endswith("yoursite.mil") or h == "docs.redhat.com", h)


if __name__ == "__main__":
    unittest.main()
