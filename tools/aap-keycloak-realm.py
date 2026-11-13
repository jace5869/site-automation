#!/usr/bin/env python3
"""AAP's own Keycloak realm, made by a script (docs/AAP_KEYCLOAK.md, part 4).

It makes ONE new realm in the Keycloak you already run - by default "aap" - and what AAP's login needs in it:

  1. the realm
  2. the card (X.509 client certificate) browser flow: a copy of Keycloak's "browser" flow with the step
     "X509/Validate Username Form" in it, set up to find the person from the certificate
  3. the client for AAP (confidential, the standard flow only, the gateway's redirect URI)
  4. what AAP reads from the token: preferred_username, email, groups
  5. the groups AAP's maps use
  6. the people: a read-only LDAP / Active Directory federation (LDAP_URL) that admits only the members of one
     group (LDAP_GROUP_DN), with a sync on a timer, and the AD groups that become AAP's teams (LDAP_TEAM_GROUPS);
     or people from a list (AAP_USERS_FILE)

Without --apply it changes nothing: it reads, and prints every change it would make. With --apply it makes them.
A second --apply finds nothing to change. It never deletes anything, and never renames anything.

What is THERE is never changed by itself - a realm you set up by hand keeps what you made:
  missing            it is made (CREATE), with --apply
  there, as wanted   ok
  there, another way "differs <thing>: <field> is X, settings say Y" - and it is left as it is. Only with
                     --update-existing are those fields, and no others, changed to what the settings say.
Things are found by what they are, not by their name: the realm's LDAP federation, its group mapper, the mapper
that reads the card's attribute, the client's group mapper, a browser flow that has the card step already.

It never touches another realm. Every request goes to /admin/realms/<the realm>... - the only others are the
login (the token request of the admin realm) and the one request that creates the realm (POST /admin/realms).
A request to any other address is refused inside the script before it is sent; the last line of a run says so.
A realm that is already there and was not made by this script: a dry run reads it, --apply needs --adopt.
It calls nothing outside: only Keycloak (and the LDAP server, for LDAP_CA_FILE). A proxy in the environment is not used.

Two ways to reach Keycloak, picked by itself (--via):
  rest    Keycloak's admin REST API at KEYCLOAK_ADMIN_URL, from this machine
  kcadm   kcadm.sh inside the Keycloak container (podman or docker exec), on the Keycloak host

Usage:
  tools/aap-keycloak-realm.py --settings aap-keycloak.env             # dry run: prints the changes
  tools/aap-keycloak-realm.py --settings aap-keycloak.env --apply     # makes them

Options:
  --settings FILE     a file of NAME=value lines (tools/aap-keycloak.env.example). The environment wins over it.
  --apply             make the changes. Without it: a dry run.
  --dry-run           the default; here so a command can say it.
  --via WAY           auto (default), rest or kcadm
  --adopt             with --apply: work in a realm of this name that this script did not make. It adds a mark to
                      the realm (one attribute) and changes nothing else of it. Never needed for a new realm.
  --secret-file FILE  with --apply: write the AAP client's secret to FILE (mode 600), for AAP's "OIDC Secret" field.
  --update-existing   change the fields that differ in things that are there (the "differs" lines). Without it
                      they are only listed.
  --sync              with --apply and LDAP_URL: one full sync of the directory at the end, and its result
  --compare FILE      print how this realm differs from a realm export (JSON) of another Keycloak - the one that
                      works. It reads only: nothing is changed, and nothing is ever imported. Secrets (masked in
                      an export) and ids are not compared.
  -h, --help          this text

The admin password is never an argument. It is read from KEYCLOAK_ADMIN_PASSWORD_FILE (a file only you can
read), or asked for at the prompt. It is not printed, and not written anywhere. The same holds for the bind
account's password (LDAP_BIND_PASSWORD_FILE): it goes to Keycloak once, when the federation is made.

Settings (the environment, or the --settings file):
@SETTINGS@
Exit code: 0 done (or nothing to do), 1 Keycloak refused or could not be reached, 2 a setting is missing or wrong.
"""
import csv
import getpass
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

MARK = "site-automation-aap-realm"          # the realm attribute this script leaves, so it knows its own realm again
X509 = "auth-x509-client-username-form"
SETTINGS = [
    ("KEYCLOAK_URL", "", "Keycloak's address as people's browsers use it, without /realms/... Required."),
    ("KEYCLOAK_ADMIN_URL", "", "where this script reaches the admin API (rest). Default: KEYCLOAK_URL. On the\n"
     "Keycloak host itself often http://localhost:8080"),
    ("KEYCLOAK_REALM", "aap", "the NEW realm. Never the realm STIG Manager uses."),
    ("KEYCLOAK_PROTECTED_REALMS", "master", "realms this script refuses to work in, with commas. Add STIG Manager's."),
    ("KEYCLOAK_ADMIN_REALM", "master", "the realm the admin account logs in to"),
    ("KEYCLOAK_ADMIN_USER", "admin", "the admin account"),
    ("KEYCLOAK_ADMIN_PASSWORD_FILE", "", "a file with its password on the first line. Empty: asked at the prompt."),
    ("CA_FILE", "", "your CA chain (PEM), for https to the admin API. Default: this machine's trust store."),
    ("KEYCLOAK_TIMEOUT", "20", "seconds per request"),
    ("KEYCLOAK_CONTAINER", "", "kcadm: the Keycloak container's name. Empty: the one running container with\n"
     "\"keycloak\" in its name."),
    ("KEYCLOAK_RUNTIME", "", "kcadm: podman or docker. Empty: the one that is installed."),
    ("KEYCLOAK_KCADM", "/opt/keycloak/bin/kcadm.sh", "kcadm: the tool's path in the container"),
    ("KEYCLOAK_KCADM_SERVER", "http://localhost:8080", "kcadm: Keycloak's address as seen inside the container"),
    ("AAP_URL", "", "the address people type for AAP, https, no path. Required."),
    ("KEYCLOAK_CLIENT_ID", "aap", "the client made for AAP"),
    ("AAP_REDIRECT_URI", "", "the exact \"OIDC Callback URL\" AAP shows. Empty: AAP_URL/api/gateway/social/complete/*"),
    ("AAP_GROUPS", "", "the groups to make, with commas: the ones AAP's maps name.\n"
     "Default: aap-admins,aap-operators,aap-users - and none with LDAP_URL (the groups then come from the directory)."),
    ("AAP_GROUPS_CLAIM", "groups", "the token claim for the groups (AAP's \"Groups Claim\" field)"),
    ("X509_FLOW", "browser-x509", "the name of the card flow in the realm"),
    ("X509_IDENTITY", "", "what in the certificate names the person: cn, edipi, upn, email or regex.\n"
     "Default: upn with LDAP_URL (the card's Principal Name, AD's userPrincipalName), cn without."),
    ("X509_REGEX", "", "X509_IDENTITY=regex: the expression run on the subject DN; its first group is the name"),
    ("X509_USER_ATTRIBUTE", "", "the user attribute the card's value is looked up in. \"username\": the person's user\n"
     "name (or email). Default: username - and aap-upn with LDAP_URL and X509_IDENTITY=upn (a mapper of the\n"
     "federation that reads LDAP_CARD_ATTRIBUTE into another attribute already is used as it is)."),
    ("X509_PASSWORD_FORM", "off", "off: the card is the only way in. on: Keycloak's password form stays, after the card."),
    ("X509_CONFIRM_PAGE", "off", "off: straight in after the card. on: Keycloak's \"continue as ...\" page is shown."),
    ("X509_OCSP", "off", "on: Keycloak asks the certificate's OCSP responder at every login"),
    ("X509_OCSP_RESPONDER", "", "X509_OCSP=on: a responder to ask in place of the certificate's own"),
    ("X509_CRL", "off", "on: Keycloak reads the CRL the certificate names (its CRL distribution point)"),
    ("AAP_USERS_FILE", "", "optional: a CSV of people to make - username,email,first name,last name,groups\n"
     "(groups with ;), and a sixth column for the value of X509_USER_ATTRIBUTE when that is set.\n"
     "Stock Keycloak does not make a user at a first card login."),
    ("LDAP_URL", "", "optional: your LDAP / AD server, e.g. ldaps://dc01.corp.example.mil:636 - a READ-ONLY user\n"
     "federation. The people then come from it; Keycloak never writes to it."),
    ("LDAP_NAME", "directory", "the federation's name in the realm. A realm that has one LDAP federation already: that\n"
     "one is used, under its own name."),
    ("LDAP_VENDOR", "ad", "ad, rhds or other"),
    ("LDAP_STARTTLS", "off", "on: LDAP_URL is ldap:// and the connection is encrypted with StartTLS. ldaps:// needs nothing."),
    ("LDAP_CA_FILE", "", "the CA chain (PEM) that signed the LDAP server's certificate. The script checks the server's\n"
     "certificate against it from this machine. Keycloak itself reads its own trust store (--truststore-paths)."),
    ("LDAP_USERS_DN", "", "where the people are, e.g. OU=People,DC=corp,DC=example,DC=mil. Required with LDAP_URL."),
    ("LDAP_BIND_DN", "", "the read-only account Keycloak binds with. Required with LDAP_URL."),
    ("LDAP_BIND_PASSWORD_FILE", "", "a file with its password. Empty: asked at the prompt (only when the federation is made)."),
    ("LDAP_GROUP_DN", "", "the group whose members may log in (the MIM-managed group), its full DN, e.g.\n"
     "CN=AAP-Access,OU=Groups,DC=corp,DC=example,DC=mil. Only its members are found and taken in.\n"
     "Empty: everybody under LDAP_USERS_DN."),
    ("LDAP_GROUP_NESTED", "on", "on: a member of a group inside LDAP_GROUP_DN counts too (Active Directory only).\n"
     "off: direct members only."),
    ("LDAP_USERNAME_ATTRIBUTE", "", "the LDAP attribute that becomes the user name, when the federation is made.\n"
     "Default: sAMAccountName for ad, uid otherwise. With X509_USER_ATTRIBUTE=username and X509_IDENTITY=upn:\n"
     "userPrincipalName, for it must then hold what the card gives."),
    ("LDAP_CARD_ATTRIBUTE", "", "the LDAP attribute that holds what the card gives. It is read into the user attribute\n"
     "X509_USER_ATTRIBUTE. Default: userPrincipalName with X509_IDENTITY=upn, mail with email."),
    ("LDAP_SYNC_CHANGED_SECONDS", "900", "Keycloak reads the changed people every so many seconds. 0: never."),
    ("LDAP_SYNC_FULL_SECONDS", "86400", "Keycloak reads all the people every so many seconds. 0: never."),
    ("LDAP_TEAM_GROUPS", "", "the AD groups that become AAP's teams and organizations: full DNs with ; between\n"
     "them. Each becomes a Keycloak group of the same name (its CN), and is in the token's groups."),
    ("LDAP_TEAM_MAPPER", "aap-teams", "the name of the group mapper made for LDAP_TEAM_GROUPS. A federation that has one\n"
     "group mapper already: that one is used, under its own name."),
]
DEFAULTS = dict((k, d) for k, d, _ in SETTINGS)
# the settings of tools/aap-nginx-blocks.py and tools/aap-keycloak-check.sh: one file serves all three
OTHER = ("AAP_UPSTREAM NGINX_CONTAINER NGINX_CONF NGINX_LISTEN NGINX_TLS_DIR AAP_TLS_CERT AAP_TLS_KEY NGINX_CLIENT_CA "
         "NGINX_VERIFY_DEPTH KEYCLOAK_NGINX KEYCLOAK_UPSTREAM KEYCLOAK_TLS_CERT KEYCLOAK_TLS_KEY KEYCLOAK_VERIFY_HEADER "
         "REPORT_WEB_FOLDER REPORTS REPORTS_PATH REPORTS_URL REPORTS_TLS_CERT REPORTS_TLS_KEY REPORTS_MOUNT "
         "REPORTS_UPSTREAM STIGMAN_URL AAP_TOKEN_FILE CHECK_TIMEOUT CARD_CA_FILE CHECK_USER").split()


def settings_help():
    out = []
    for k, d, h in SETTINGS:
        first, _, rest = h.partition("\n")
        out.append("  %-28s %s%s" % (k, first, (" Default: %s" % d) if d and "Default" not in h else ""))
        out += ["  %-28s %s" % ("", line) for line in rest.split("\n") if line]
    return "\n".join(out) + "\n"


class Stop(Exception):
    """A plain sentence for the person, and the exit code."""

    def __init__(self, text, code=1):
        Exception.__init__(self, text)
        self.code = code


def on(v):
    return str(v).strip().lower() in ("on", "true", "yes", "1")


def names(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def read_settings(path, env):
    s = dict(DEFAULTS)
    given = set()
    if path:
        try:
            with open(path) as f:
                for n, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    k, eq, v = line.partition("=")
                    k = k.strip()
                    if k.startswith("export "):
                        k = k[7:].strip()
                    if not eq or (k not in DEFAULTS and k not in OTHER):
                        raise Stop("%s line %d: not a setting: %s" % (path, n, k), 2)
                    v = v.strip()
                    if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
                        v = v[1:-1]
                    if k in DEFAULTS:
                        s[k] = v
                        if v != "":
                            given.add(k)
        except (IOError, OSError) as e:
            raise Stop("The settings file cannot be read: %s" % e, 2)
    for k in DEFAULTS:
        if env.get(k, "") != "":
            s[k] = env[k]
            given.add(k)
    for k in ("KEYCLOAK_URL", "KEYCLOAK_ADMIN_URL", "AAP_URL"):
        s[k] = s[k].rstrip("/")
    s["KEYCLOAK_ADMIN_URL"] = s["KEYCLOAK_ADMIN_URL"] or s["KEYCLOAK_URL"]
    # what the settings do not say: the defaults that follow from the others. "_set": the names the settings do say.
    s["_set"] = given
    s["X509_IDENTITY"] = s["X509_IDENTITY"] or ("upn" if s["LDAP_URL"] else "cn")
    if s["X509_USER_ATTRIBUTE"].lower() == "username":
        s["X509_USER_ATTRIBUTE"] = ""
    elif not s["X509_USER_ATTRIBUTE"] and s["LDAP_URL"] and s["X509_IDENTITY"] == "upn":
        s["X509_USER_ATTRIBUTE"] = "aap-upn"
        given.discard("X509_USER_ATTRIBUTE")
    if s["LDAP_URL"] and s["X509_USER_ATTRIBUTE"] and not s["LDAP_CARD_ATTRIBUTE"]:
        s["LDAP_CARD_ATTRIBUTE"] = {"upn": "userPrincipalName", "email": "mail"}.get(s["X509_IDENTITY"], "")
    if not s["AAP_GROUPS"] and not s["LDAP_URL"]:
        s["AAP_GROUPS"] = "aap-admins,aap-operators,aap-users"
    return s


def check_settings(s):
    def bad(text):
        raise Stop(text + " (see --help)", 2)
    if not s["AAP_URL"]:
        bad("Set AAP_URL, e.g. AAP_URL=https://aap.yoursite.mil")
    if not re.match(r"^https://[^/]+$", s["AAP_URL"]):
        bad("AAP_URL must be https:// and a name with no path - the address people type")
    if not s["KEYCLOAK_URL"]:
        bad("Set KEYCLOAK_URL, e.g. KEYCLOAK_URL=https://keycloak.yoursite.mil")
    if not re.match(r"^https?://", s["KEYCLOAK_ADMIN_URL"]):
        bad("KEYCLOAK_ADMIN_URL must start with http:// or https://")
    realm = s["KEYCLOAK_REALM"]
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,35}$", realm):
        bad("KEYCLOAK_REALM must be a short name of letters, digits, - and _")
    if realm.lower() in [x.lower() for x in names(s["KEYCLOAK_PROTECTED_REALMS"]) + ["master", s["KEYCLOAK_ADMIN_REALM"]]]:
        bad("KEYCLOAK_REALM=%s is a protected realm (KEYCLOAK_PROTECTED_REALMS, the admin realm). AAP gets a realm of its own" % realm)
    if s["X509_USER_ATTRIBUTE"] and not re.match(r"^[A-Za-z][A-Za-z0-9._-]{0,60}$", s["X509_USER_ATTRIBUTE"]):
        bad("X509_USER_ATTRIBUTE must be one short name of letters, digits, . - and _")
    for k in ("X509_FLOW", "KEYCLOAK_CLIENT_ID"):
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,60}$", s[k]) or ".." in s[k]:
            bad("%s must be a short name of letters, digits, spaces, . - and _" % k)
    if s["X509_IDENTITY"] not in ("cn", "edipi", "upn", "email", "regex"):
        bad("X509_IDENTITY must be cn, edipi, upn, email or regex")
    if s["X509_IDENTITY"] == "regex":
        try:
            if re.compile(s["X509_REGEX"]).groups < 1:
                raise ValueError("no group")
        except (re.error, ValueError):
            bad("X509_IDENTITY=regex needs X509_REGEX with one group in ( ), e.g. CN=(.*?)(?:,|$)")
    if s["AAP_REDIRECT_URI"] and not s["AAP_REDIRECT_URI"].startswith(s["AAP_URL"] + "/"):
        bad("AAP_REDIRECT_URI must start with AAP_URL/")
    for k in ("CA_FILE", "KEYCLOAK_ADMIN_PASSWORD_FILE", "AAP_USERS_FILE", "LDAP_BIND_PASSWORD_FILE", "LDAP_CA_FILE"):
        if s[k] and not os.access(s[k], os.R_OK):
            bad("%s=%s cannot be read" % (k, s[k]))
    if s["LDAP_URL"]:
        if not re.match(r"^ldaps?://", s["LDAP_URL"]):
            bad("LDAP_URL must start with ldaps:// (or ldap://)")
        if not s["LDAP_USERS_DN"] or not s["LDAP_BIND_DN"]:
            bad("LDAP_URL needs LDAP_USERS_DN and LDAP_BIND_DN")
        if s["LDAP_VENDOR"] not in ("ad", "rhds", "other"):
            bad("LDAP_VENDOR must be ad, rhds or other")
        tls = s["LDAP_URL"].startswith("ldaps://")
        if tls and on(s["LDAP_STARTTLS"]):
            bad("LDAP_STARTTLS=on goes with an ldap:// address. ldaps:// is encrypted already: take LDAP_STARTTLS out")
        if not tls and not on(s["LDAP_STARTTLS"]):
            bad("LDAP_URL=ldap://... would send the bind account's password unencrypted. Use ldaps://, or LDAP_STARTTLS=on")
        for k in ("LDAP_USERS_DN", "LDAP_BIND_DN", "LDAP_GROUP_DN"):
            if s[k] and not is_dn(s[k]):
                bad("%s must be a full DN, e.g. %s=CN=...,DC=corp,DC=example,DC=mil" % (k, k))
        for dn in team_groups(s):
            if not is_dn(dn):
                bad("LDAP_TEAM_GROUPS: not a full DN: %s. Put ; between the groups (a DN has commas in it)" % dn)
        if team_groups(s) and not common_base(team_groups(s)):
            bad("LDAP_TEAM_GROUPS: the groups have no common part (they must be in one directory)")
        for k in ("LDAP_SYNC_CHANGED_SECONDS", "LDAP_SYNC_FULL_SECONDS"):
            if not re.match(r"^\d{1,8}$", s[k]):
                bad("%s must be a whole number of seconds (0: never)" % k)
        for k in ("LDAP_NAME", "LDAP_TEAM_MAPPER"):
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,60}$", s[k]):
                bad("%s must be a short name of letters, digits, spaces, . - and _" % k)
        if s["X509_USER_ATTRIBUTE"] and not s["LDAP_CARD_ATTRIBUTE"]:
            bad("X509_USER_ATTRIBUTE with LDAP_URL needs LDAP_CARD_ATTRIBUTE: the LDAP attribute that holds what the card gives")
        if s["LDAP_CARD_ATTRIBUTE"] and not s["X509_USER_ATTRIBUTE"]:
            bad("LDAP_CARD_ATTRIBUTE needs X509_USER_ATTRIBUTE: the name of the user attribute it is read into (not \"username\")")
        if s["LDAP_CARD_ATTRIBUTE"] and not re.match(r"^[A-Za-z][A-Za-z0-9-]{0,60}$", s["LDAP_CARD_ATTRIBUTE"]):
            bad("LDAP_CARD_ATTRIBUTE must be the name of one LDAP attribute")
    elif on(s["LDAP_STARTTLS"]) or s["LDAP_GROUP_DN"] or s["LDAP_TEAM_GROUPS"]:
        bad("LDAP_GROUP_DN, LDAP_TEAM_GROUPS and LDAP_STARTTLS need LDAP_URL")
    try:
        if float(s["KEYCLOAK_TIMEOUT"]) <= 0:
            raise ValueError()
    except ValueError:
        bad("KEYCLOAK_TIMEOUT must be a number of seconds")


def split_dn(dn):
    """The DN's parts, split at the commas that are not escaped."""
    return [p.strip() for p in re.split(r"(?<!\\),", dn) if p.strip()]


def is_dn(v):
    parts = split_dn(v)
    return len(parts) > 1 and all(re.match(r"^[A-Za-z][A-Za-z0-9.-]*\s*=\s*\S", p) for p in parts)


def team_groups(s):
    return [x.strip() for x in re.split(r"[;\n]", s["LDAP_TEAM_GROUPS"]) if x.strip()]


def common_base(dns):
    """The longest DN all of them end with, above each group itself."""
    rev = [list(reversed(split_dn(d)))[:-1] for d in dns]
    base = []
    for parts in zip(*rev):
        if len(set(p.lower() for p in parts)) != 1:
            break
        base.append(parts[0])
    return ",".join(reversed(base))


def filter_value(v):
    """A value inside an LDAP filter (RFC 4515): its special characters escaped."""
    return "".join({"\\": "\\5c", "*": "\\2a", "(": "\\28", ")": "\\29", "\0": "\\00"}.get(c, c) for c in v)


def ldap_username_attribute(s):
    if s["LDAP_USERNAME_ATTRIBUTE"]:
        return s["LDAP_USERNAME_ATTRIBUTE"]
    if s["LDAP_VENDOR"] != "ad":
        return "uid"
    return "userPrincipalName" if s["X509_IDENTITY"] == "upn" and not s["X509_USER_ATTRIBUTE"] else "sAMAccountName"


IN_CHAIN = "1.2.840.113556.1.4.1941"          # Active Directory's matching rule "in chain": membership through nested groups


def nested(s):
    return on(s["LDAP_GROUP_NESTED"]) and s["LDAP_VENDOR"] == "ad"


def member_filter(s):
    """The user filter that lets in only the members of LDAP_GROUP_DN ("" without one)."""
    if not s["LDAP_GROUP_DN"]:
        return ""
    return "(memberOf%s=%s)" % (":%s:" % IN_CHAIN if nested(s) else "", filter_value(s["LDAP_GROUP_DN"]))


def secret_from(path, prompt):
    if path:
        with open(path) as f:
            v = f.readline().rstrip("\r\n")
        if not v:
            raise Stop("%s is empty." % path, 2)
        return v
    if not sys.stdin.isatty():
        raise Stop("%s is needed: put it in a file only you can read and name the file in the settings "
                   "(there is no terminal to ask at)." % prompt, 2)
    return getpass.getpass(prompt + ": ")


# ---- the only door to Keycloak --------------------------------------------------------------------------------
def allowed(method, path, realm):
    """True for the realm's own admin addresses, and for the one request that creates a realm."""
    p = path.split("?", 1)[0]
    base = "/admin/realms/" + realm
    if ".." in p.split("/") or "//" in p or "\\" in p or "%2e" in p.lower() or "%2f" in p.lower() or "%5c" in p.lower():
        return False                                 # no way round: a path that climbs out is never sent
    if p == "/admin/realms":
        return method == "POST"
    return p == base or p.startswith(base + "/")


class Rest(object):
    """Keycloak's admin REST API."""
    name = "rest"

    def __init__(self, s, password):
        self.url, self.s, self.password, self.token = s["KEYCLOAK_ADMIN_URL"], s, password, ""
        self.ctx = None
        if self.url.startswith("https://"):
            self.ctx = ssl.create_default_context(cafile=s["CA_FILE"] or None)
        self.timeout = float(s["KEYCLOAK_TIMEOUT"])
        self.where = self.url
        # straight to Keycloak, always: a proxy named in the environment (https_proxy) is never used
        handlers = [urllib.request.ProxyHandler({})]
        if self.ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=self.ctx))
        self.opener = urllib.request.build_opener(*handlers)

    def _open(self, req):
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.getcode(), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            raise Stop("Keycloak at %s does not answer: %s" % (self.url, getattr(e, "reason", e)))

    def login(self):
        s = self.s
        body = urllib.parse.urlencode({"grant_type": "password", "client_id": "admin-cli",
                                       "username": s["KEYCLOAK_ADMIN_USER"], "password": self.password}).encode()
        code, raw = self._open(urllib.request.Request(
            "%s/realms/%s/protocol/openid-connect/token" % (self.url, urllib.parse.quote(s["KEYCLOAK_ADMIN_REALM"])), data=body))
        if code == 401:
            raise Stop("Keycloak did not accept the admin account %s (realm %s): wrong name or password."
                       % (s["KEYCLOAK_ADMIN_USER"], s["KEYCLOAK_ADMIN_REALM"]))
        if code != 200:
            raise Stop("The login at %s answered HTTP %s%s" % (self.url, code, hint_for(code, raw)))
        try:
            self.token = json.loads(raw.decode())["access_token"]
        except (ValueError, KeyError):
            raise Stop("The login at %s did not answer with a token: is this Keycloak's address?" % self.url)

    def call(self, method, path, body=None):
        for again in (True, False):
            req = urllib.request.Request(self.url + path, method=method,
                                         data=None if body is None else json.dumps(body).encode())
            req.add_header("Authorization", "Bearer " + self.token)
            req.add_header("Accept", "application/json")
            if body is not None:
                req.add_header("Content-Type", "application/json")
            code, raw = self._open(req)
            if code == 401 and again:           # the admin token lasts a minute: log in once more
                self.login()
                continue
            break
        text = raw.decode("utf-8", "replace")
        try:
            data = json.loads(text) if text.strip() else None
        except ValueError:
            data = None
        return code, data, text

    def close(self):
        pass


def hint_for(code, raw):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    if "No required SSL certificate" in text:
        return ": this address asks every caller for a card. Use KEYCLOAK_ADMIN_URL with Keycloak's own address, or --via kcadm on the Keycloak host."
    if code in (403, 404):
        return ": the admin API is not open at this address. Use KEYCLOAK_ADMIN_URL with Keycloak's own address, or --via kcadm on the Keycloak host."
    return ""


class Kcadm(object):
    """kcadm.sh inside the Keycloak container. The password goes in through the environment (KC_CLI_PASSWORD),
    never as an argument; kcadm's session file lives in the container's /tmp and is removed at the end."""
    name = "kcadm"

    def __init__(self, s, password):
        self.s, self.password = s, password
        self.runtime = s["KEYCLOAK_RUNTIME"] or next((r for r in ("podman", "docker") if shutil.which(r)), "")
        if not self.runtime or not shutil.which(self.runtime):
            raise Stop("kcadm needs podman or docker on this machine (the Keycloak host).")
        self.container = s["KEYCLOAK_CONTAINER"] or self._find()
        self.tool = s["KEYCLOAK_KCADM"]
        self.config = "/tmp/site-automation-aap-realm.%d.kcadm" % os.getpid()
        self.where = "%s exec %s %s" % (self.runtime, self.container, self.tool)

    def _find(self):
        p = subprocess.run([self.runtime, "ps", "--format", "{{.Names}}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        found = [n for n in p.stdout.split() if "keycloak" in n.lower()]
        if len(found) != 1:
            raise Stop("kcadm: %s running containers have \"keycloak\" in their name. Set KEYCLOAK_CONTAINER."
                       % ("no" if not found else "%d" % len(found)))
        return found[0]

    def _run(self, args, stdin=None, env=None):
        cmd = [self.runtime, "exec", "-i"] + (["--env", "KC_CLI_PASSWORD"] if env else []) + [self.container, self.tool] + args
        e = dict(os.environ)
        e.update(env or {})
        try:
            p = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               env=e, timeout=float(self.s["KEYCLOAK_TIMEOUT"]) * 3)
        except subprocess.TimeoutExpired:
            raise Stop("kcadm did not answer in time (%s)." % self.where)
        return p.returncode, p.stdout, p.stderr

    def login(self):
        s = self.s
        rc, out, err = self._run(["config", "credentials", "--server", s["KEYCLOAK_KCADM_SERVER"], "--realm", s["KEYCLOAK_ADMIN_REALM"],
                                  "--user", s["KEYCLOAK_ADMIN_USER"], "--config", self.config], env={"KC_CLI_PASSWORD": self.password})
        if rc != 0:
            last = (err.strip().splitlines() or out.strip().splitlines() or ["no answer"])[-1]
            if "invalid_grant" in last or "Invalid user credentials" in last:
                raise Stop("Keycloak did not accept the admin account %s (realm %s): wrong name or password."
                           % (s["KEYCLOAK_ADMIN_USER"], s["KEYCLOAK_ADMIN_REALM"]))
            raise Stop("kcadm could not log in (%s): %s" % (self.where, last))

    def call(self, method, path, body=None):
        rel = path[len("/admin/"):]
        verb = {"GET": "get", "POST": "create", "PUT": "update"}[method]
        args = [verb, rel, "--config", self.config]
        if method != "GET":
            args += ["-f", "-"] + (["-n"] if method == "PUT" else [])
        rc, out, err = self._run(args, stdin=None if method == "GET" else json.dumps({} if body is None else body))
        if rc == 0:
            try:
                return 200, (json.loads(out) if method == "GET" and out.strip() else None), out
            except ValueError:
                return 200, None, out
        text = (err.strip() or out.strip())
        code = 404 if "not found" in text.lower() else (409 if "conflict" in text.lower() else 500)
        return code, None, text

    def close(self):
        subprocess.run([self.runtime, "exec", self.container, "rm", "-f", self.config], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def show(v):
    """A value for a "differs" line."""
    if v is None:
        return "(not set)"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        v = ", ".join(str(x) for x in v)
    v = " ".join(str(v).split())
    if v == "":
        return "(empty)"
    return v if len(v) <= 160 else v[:157] + "..."


class Api(object):
    """Every request passes here: checked against the realm, counted, and held back in a dry run."""

    def __init__(self, door, realm, apply, out, update=False):
        self.door, self.realm, self.apply, self.out, self.update = door, realm, apply, out, update
        self.base = "/admin/realms/" + realm
        self.sent, self.changes, self.reads, self.differences = [], 0, 0, 0

    def _send(self, method, path, body=None):
        if not allowed(method, path, self.realm):
            raise Stop("Refused inside the script: %s %s is outside the realm %s." % (method, path, self.realm))
        self.sent.append((method, path.split("?", 1)[0]))
        return self.door.call(method, path, body)

    def get(self, rel):
        """The answer, or None when there is no such thing (404)."""
        self.reads += 1
        code, data, text = self._send("GET", self.base + rel)
        if code == 404:
            return None
        if code != 200:
            raise Stop("Keycloak answered HTTP %s to a read of %s%s%s" % (code, self.base, rel, hint_for(code, text) or ": " + text.strip()[:200]))
        return data

    def change(self, word, what, method, rel, body=None, root=False):
        """One change: printed always, made only with --apply. True when it was made."""
        self.changes += 1
        self.out("%-7s %s" % (word, what))
        if not self.apply:
            return False
        code, _, text = self._send(method, "/admin/realms" if root else self.base + rel, body)
        if code not in (200, 201, 202, 204):
            raise Stop("Keycloak refused (%s): %s - HTTP %s %s" % (what, method, code, text.strip()[:300]))
        return True

    def differ(self, what, fields, method=None, rel="", body=None):
        """A thing that is THERE and is not as the settings say. fields: (field, what it is, what the settings say).
        Without --update-existing it is only said, one line a field. With it, those fields are changed (method None:
        the caller makes the change itself). True when the change was made, or is the caller's to make."""
        if not self.update:
            for f, have, want in fields:
                self.differences += 1
                self.out("differs %s: %s is %s, settings say %s" % (what, f, show(have), show(want)))
            return False
        line = "%s: %s" % (what, "; ".join("%s %s -> %s" % (f, show(h), show(w)) for f, h, w in fields))
        if method is None:
            self.changes += 1
            self.out("%-7s %s" % ("CHANGE", line))
            return self.apply
        return self.change("CHANGE", line, method, rel, body)

    def quiet(self, method, rel, body=None):
        """A further request of a change that was already printed. Only ever with --apply."""
        if not self.apply:
            raise Stop("Refused inside the script: a change in a dry run (%s %s)." % (method, rel))
        code, _, text = self._send(method, self.base + rel, body)
        if code not in (200, 201, 202, 204):
            raise Stop("Keycloak refused: %s %s%s - HTTP %s %s" % (method, self.base, rel, code, text.strip()[:300]))

    def same(self, what):
        self.out("ok      %s" % what)

    def found(self, what):
        self.out("found   %s" % what)


def differs(have, want):
    """The keys of want whose value in have is another one (a missing key counts as empty)."""
    out = {}
    for k, v in want.items():
        h = (have or {}).get(k)
        if isinstance(v, dict):
            if differs(h or {}, v):
                out[k] = v
        elif isinstance(v, list):
            if sorted(h or []) != sorted(v):
                out[k] = v
        elif (h if h is not None else "") != v and not (v is False and h is None):
            out[k] = v
    return out


def rows(have, want, strip=""):
    """differs() as the rows of Api.differ."""
    return [(k[len(strip):] if strip and k.startswith(strip) else k, (have or {}).get(k), v) for k, v in sorted(differs(have, want).items())]


def flat(config):
    """A component's config (each value a list) as plain values."""
    return dict((k, (v or [""])[0] if isinstance(v, list) else v) for k, v in (config or {}).items())


# ---- what is made ---------------------------------------------------------------------------------------------
def x509_config(s):
    how = s["X509_IDENTITY"]
    source, regex = {
        "cn": ("Subject's Common Name", "(.*?)(?:$)"),
        "edipi": ("Match SubjectDN using regular expression", r"CN=[^,]*?\.(\d{10})(?:,|$)"),
        "upn": ("Subject's Alternative Name otherName (UPN)", "(.*?)(?:$)"),
        "email": ("Subject's Alternative Name E-mail", "(.*?)(?:$)"),
        "regex": ("Match SubjectDN using regular expression", s["X509_REGEX"]),
    }[how]
    attr = s["X509_USER_ATTRIBUTE"]
    c = {
        "x509-cert-auth.mapping-source-selection": source,
        "x509-cert-auth.regular-expression": regex,
        "x509-cert-auth.mapper-selection": "Custom Attribute Mapper" if attr else "Username or Email",
        "x509-cert-auth.mapper-selection.user-attribute-name": attr or "usercertificate",
        "x509-cert-auth.canonical-dn-enabled": "false",
        "x509-cert-auth.serialnumber-hex-enabled": "false",
        "x509-cert-auth.timestamp-validation-enabled": "true",
        "x509-cert-auth.confirmation-page-disallowed": "false" if on(s["X509_CONFIRM_PAGE"]) else "true",
        "x509-cert-auth.ocsp-checking-enabled": "true" if on(s["X509_OCSP"]) else "false",
        "x509-cert-auth.ocsp-responder-uri": s["X509_OCSP_RESPONDER"] if on(s["X509_OCSP"]) else "",
        "x509-cert-auth.crl-checking-enabled": "true" if on(s["X509_CRL"]) else "false",
        "x509-cert-auth.crldp-checking-enabled": "true" if on(s["X509_CRL"]) else "false",
    }
    return c


def x509_compared(s):
    """x509_config without the fields that mean nothing with these settings: they are not compared in a flow that is there."""
    c = x509_config(s)
    if not s["X509_USER_ATTRIBUTE"]:
        del c["x509-cert-auth.mapper-selection.user-attribute-name"]
    if not on(s["X509_OCSP"]):
        del c["x509-cert-auth.ocsp-responder-uri"]
    return c


def ensure_realm(api, s, adopt):
    """Returns (there, fresh): the realm is there now; it is made in this run."""
    realm = s["KEYCLOAK_REALM"]
    want = {"enabled": True, "registrationAllowed": False, "resetPasswordAllowed": False}
    have = api.get("")
    if have is None:
        body = dict(want, realm=realm, displayName="Ansible Automation Platform", rememberMe=False, attributes={MARK: "1"})
        api.change("CREATE", "realm %s" % realm, "POST", "", body, root=True)
        return api.apply, True
    if (have.get("attributes") or {}).get(MARK) != "1":
        if adopt:
            api.change("CREATE", "realm %s: the mark that this is AAP's realm (--adopt). Nothing else of the realm changes by it" % realm,
                       "PUT", "", dict(have, attributes=dict(have.get("attributes") or {}, **{MARK: "1"})))
            # the realm goes back whole, as it was read: Keycloak keeps some of a realm's own settings (its browser
            # security headers) as attributes it does not list, and drops them when only the listed ones come back
        elif api.apply:
            raise Stop("The realm %s is already there and this script did not make it. Nothing was changed. Pick another "
                       "KEYCLOAK_REALM - or, when this really is AAP's realm, add --adopt: it marks the realm as AAP's, and "
                       "from then on --apply adds what is missing in it." % realm, 2)
        else:
            api.out("note    the realm %s is there and this script did not make it. A dry run only reads it; --apply needs --adopt." % realm)
    d = rows(have, want)
    if d:
        api.differ("realm %s" % realm, d, "PUT", "", dict(differs(have, want), realm=realm))
    else:
        api.same("realm %s" % realm)
    return True, False


def card_flow(api, s, there):
    """The flow to work in: X509_FLOW - or, when the settings do not name one and there is none of that name, the
    realm's own browser flow when a card step is in it already (a realm set up by hand)."""
    flow = s["X509_FLOW"]
    if not there:
        return flow, []
    flows = api.get("/authentication/flows") or []
    aliases = [f.get("alias") for f in flows]
    if flow in aliases or "X509_FLOW" in s["_set"]:
        return flow, flows
    bound = (api.get("") or {}).get("browserFlow") or ""
    if bound and bound != "browser" and bound in aliases:
        ex = api.get("/authentication/flows/%s/executions" % urllib.parse.quote(bound)) or []
        if any(e.get("providerId") == X509 for e in ex):
            api.found("flow %s: the realm's browser flow, with the card step in it. It is used as it is (X509_FLOW=%s is not there)"
                      % (bound, flow))
            return bound, flows
    return flow, flows


def ensure_flow(api, s, there, flow, flows):
    q = urllib.parse.quote(flow)
    want = x509_config(s)
    forms_want = "ALTERNATIVE" if on(s["X509_PASSWORD_FORM"]) else "DISABLED"
    plan = ["flow %s: a copy of the browser flow" % flow,
            "flow %s: the step X509/Validate Username Form, before the password forms, ALTERNATIVE" % flow,
            "flow %s: the card names the person by %s" % (flow, identity_words(s)),
            "flow %s: the password forms %s" % (flow, forms_want)]
    fresh_flow = False
    if not there or not any(f.get("alias") == flow for f in flows or []):
        made = api.change("CREATE", plan[0], "POST", "/authentication/flows/browser/copy", {"newName": flow})
        if not made:
            for line in plan[1:]:
                api.change("CHANGE" if line is plan[3] else "CREATE", line, "POST", "")
            return
        fresh_flow = True
    else:
        api.same(plan[0])

    def steps():
        return api.get("/authentication/flows/%s/executions" % q) or []

    def top(ex):
        return [e for e in ex if e.get("level") == 0]
    ex = steps()
    mine = [e for e in ex if e.get("providerId") == X509]
    found = bool(mine)
    if not mine:
        api.change("CREATE", plan[1], "POST", "/authentication/flows/%s/executions/execution" % q, {"provider": X509})
        if not api.apply:
            for line in plan[2:]:
                api.change("CHANGE" if line is plan[3] else "CREATE", line, "POST", "")
            return
        ex = steps()
        mine = [e for e in top(ex) if e.get("providerId") == X509]
    fresh = not found
    x = mine[0]

    def before_forms(ex):
        t = top(ex)
        flows_at = [i for i, e in enumerate(t) if e.get("authenticationFlow")]
        at = [i for i, e in enumerate(t) if e.get("id") == x["id"]]
        return not at or not flows_at or at[0] < flows_at[0]           # a step inside a sub-flow is left where it is
    place_ok = before_forms(ex)
    if not place_ok and (fresh or api.differ("flow %s: the X.509 step" % flow, [("its place", "after the password forms", "before them")])):
        guard = 0
        while not before_forms(ex):
            guard += 1
            if guard > 20:
                raise Stop("The X.509 step of the flow %s could not be moved before the password forms." % flow)
            api.quiet("POST", "/authentication/executions/%s/raise-priority" % x["id"], {})
            ex = steps()
        place_ok = True
    x = [e for e in ex if e.get("id") == x["id"]][0]
    req = x.get("requirement")
    if fresh:                                    # part of making the step: Keycloak adds a new step as DISABLED
        if req != "ALTERNATIVE":
            api.quiet("PUT", "/authentication/flows/%s/executions" % q, dict(x, requirement="ALTERNATIVE"))
    elif req not in ("ALTERNATIVE", "REQUIRED"):
        api.differ("flow %s: the X.509 step" % flow, [("requirement", req, "ALTERNATIVE")], "PUT",
                   "/authentication/flows/%s/executions" % q, dict(x, requirement="ALTERNATIVE"))
    elif place_ok:
        api.same(plan[1] if req == "ALTERNATIVE" and x.get("level") == 0 else
                 "flow %s: the step X509/Validate Username Form, %s%s" % (flow, req, "" if x.get("level") == 0 else ", inside a sub-flow"))
    # the card's settings
    cid = x.get("authenticationConfig")
    alias = "%s card" % flow
    if not cid:
        api.change("CREATE", plan[2], "POST", "/authentication/executions/%s/config" % x["id"], {"alias": alias, "config": want})
    else:
        have = api.get("/authentication/config/%s" % cid) or {}
        cmp_ = x509_compared(s)
        # a check the console never switched on is not in the step's settings at all: that is "false"
        had = dict({k: "false" for k, v in cmp_.items() if v == "false"}, **(have.get("config") or {}))
        d = rows(had, cmp_, "x509-cert-auth.")
        if d:
            api.differ("flow %s: the card step" % flow, d, "PUT", "/authentication/config/%s" % cid,
                       {"id": cid, "alias": have.get("alias") or alias,
                        "config": dict(have.get("config") or {}, **differs(had, cmp_))})
        else:
            api.same(plan[2])
    # the password forms after it
    forms = [e for e in top(ex) if e.get("authenticationFlow") and str(e.get("displayName") or "").lower().endswith("forms")]
    for e in forms:
        if e.get("requirement") == forms_want:
            api.same(plan[3])
        elif fresh_flow:
            api.change("CHANGE", plan[3], "PUT", "/authentication/flows/%s/executions" % q, dict(e, requirement=forms_want))
        else:
            api.differ("flow %s: the password forms (%s)" % (flow, e.get("displayName")),
                       [("requirement", e.get("requirement"), forms_want)], "PUT",
                       "/authentication/flows/%s/executions" % q, dict(e, requirement=forms_want))
    if not forms and not fresh_flow:
        api.out("note    flow %s has no sub-flow named \"... forms\": whether a password form is in it was not checked (X509_PASSWORD_FORM)"
                % flow)


def identity_words(s):
    w = {"cn": "the subject's common name (DOE.JOHN.Q.1234567890)", "edipi": "the 10 digits at the end of the common name (the EDIPI)",
         "upn": "the UPN in the subject alternative name (1234567890123456@mil)", "email": "the email in the subject alternative name",
         "regex": "X509_REGEX on the subject DN"}[s["X509_IDENTITY"]]
    return w + (", looked up in the user attribute %s" % s["X509_USER_ATTRIBUTE"] if s["X509_USER_ATTRIBUTE"] else ", as the user name")


def ensure_attribute(api, s, there):
    """Keycloak keeps only the user attributes its user profile names: X509_USER_ATTRIBUTE is added there, for admins only."""
    attr = s["X509_USER_ATTRIBUTE"]
    label = "realm %s: the user attribute %s (seen and set by admins only)" % (s["KEYCLOAK_REALM"], attr)
    prof = api.get("/users/profile") if there else None
    if prof is not None and any(a.get("name") == attr for a in prof.get("attributes") or []):
        api.same("realm %s: the user attribute %s is in the user profile" % (s["KEYCLOAK_REALM"], attr))
        return
    if prof is not None and prof.get("unmanagedAttributePolicy") in ("ENABLED", "ADMIN_EDIT"):
        api.same("realm %s: the user attribute %s is kept (the realm keeps attributes its user profile does not name)"
                 % (s["KEYCLOAK_REALM"], attr))
        return
    body = dict(prof or {})
    body["attributes"] = list(body.get("attributes") or []) + [
        {"name": attr, "displayName": attr, "permissions": {"view": ["admin"], "edit": ["admin"]}, "multivalued": False}]
    api.change("CREATE", label, "PUT", "/users/profile", body)


def bind_flow(api, s, there, fresh, flow):
    realm = s["KEYCLOAK_REALM"]
    have = api.get("") if there else None
    if have is not None and have.get("browserFlow") == flow:
        api.same("realm %s: its browser flow is %s" % (realm, flow))
    elif fresh or have is None:
        api.change("CHANGE", "realm %s: its browser flow is %s" % (realm, flow), "PUT", "", {"realm": realm, "browserFlow": flow})
    else:
        api.differ("realm %s" % realm, [("browserFlow", have.get("browserFlow"), flow)], "PUT", "", {"realm": realm, "browserFlow": flow})


def redirect_uri(s):
    return s["AAP_REDIRECT_URI"] or s["AAP_URL"] + "/api/gateway/social/complete/*"


def ensure_client(api, s, there):
    cid = s["KEYCLOAK_CLIENT_ID"]
    want = {"enabled": True, "protocol": "openid-connect", "publicClient": False, "standardFlowEnabled": True,
            "implicitFlowEnabled": False, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
            "redirectUris": [redirect_uri(s)]}
    claim = s["AAP_GROUPS_CLAIM"]
    tok = {"id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"}
    mappers = [
        ("groups", "oidc-group-membership-mapper", dict(tok, **{"claim.name": claim, "full.path": "false"})),
        ("audience", "oidc-audience-mapper", {"included.client.audience": cid, "access.token.claim": "true"}),
    ]
    what = "client %s (confidential, standard flow only, redirect URI %s)" % (cid, redirect_uri(s))
    found = api.get("/clients?clientId=%s" % urllib.parse.quote(cid)) if there else None
    if not found:
        made = api.change("CREATE", what, "POST", "/clients", dict(want, clientId=cid, name="Ansible Automation Platform",
                                                                   rootUrl=s["AAP_URL"], baseUrl=s["AAP_URL"], webOrigins=[]))
        if not made:
            for n, _, _ in mappers:
                api.change("CREATE", "client %s: the mapper %s" % (cid, n), "POST", "")
            api.same("client %s: preferred_username and email come from the realm's profile and email scopes" % cid)
            return None
        found = api.get("/clients?clientId=%s" % urllib.parse.quote(cid))
    else:
        d = rows(found[0], want)
        if d:
            api.differ("client %s" % cid, d, "PUT", "/clients/%s" % found[0]["id"], dict(found[0], **differs(found[0], want)))
        else:
            api.same(what)
    c = found[0]
    own = "/clients/%s/protocol-mappers/models" % c["id"]
    scopes = dict((x.get("name"), x) for x in (api.get("/client-scopes") or []))
    mine = set(x.get("name") for x in (api.get("/clients/%s/default-client-scopes" % c["id"]) or []))
    # every mapper the client's tokens get: its own, then those of its default scopes. Found by kind, not by name.
    have = [(own, m, "") for m in (api.get(own) or [])]
    for name in sorted(mine):
        sc = scopes.get(name) or {}
        have += [("/client-scopes/%s/protocol-mappers/models" % sc.get("id"), m, name) for m in sc.get("protocolMappers") or []]
    names_have = set(m.get("name") for _, m, sc in have if not sc)
    for n, kind, cfg in mappers:
        key = "claim.name" if n == "groups" else "included.client.audience"
        of_kind = [h for h in have if h[1].get("protocolMapper") == kind]
        pick = ([h for h in of_kind if (h[1].get("config") or {}).get(key) == cfg[key]]
                or [h for h in of_kind if h[1].get("name") == n and not h[2]] or (of_kind[:1] if n == "groups" else []))
        say = "the groups, short names, as the claim \"%s\" in the ID token, the access token and userinfo" % claim
        if not pick:
            api.change("CREATE", "client %s: the mapper %s%s" % (cid, n, " (%s)" % say if n == "groups" else ""), "POST", own,
                       {"name": n, "protocol": "openid-connect", "protocolMapper": kind,
                        "config": dict(cfg, **({"id.token.claim": "false"} if n == "audience" else {}))})
            continue
        path, m, sc = pick[0]
        label = "client %s: the mapper %s%s" % (cid, m.get("name"), " of the scope %s" % sc if sc else "")
        d = rows(m.get("config") or {}, cfg)
        if d:
            api.differ(label, d, "PUT", "%s/%s" % (path, m["id"]), dict(m, config=dict(m.get("config") or {}, **differs(m.get("config") or {}, cfg))))
        else:
            api.same(label + (" (%s)" % say if n == "groups" else ""))
    # preferred_username and email: the realm's own "profile" and "email" scopes carry them
    for scope, claim_name, attr in (("profile", "preferred_username", "username"), ("email", "email", "email")):
        label = "client %s: %s in the token" % (cid, claim_name)
        carried = [h for h in have if (h[1].get("config") or {}).get("claim.name") == claim_name]
        sc = scopes.get(scope)
        offers = sc is not None and any((m.get("config") or {}).get("claim.name") == claim_name for m in sc.get("protocolMappers") or [])
        if carried:
            api.same(label + (" (the scope %s)" % carried[0][2] if carried[0][2] else " (its own mapper)"))
        elif offers:
            api.change("CREATE", label + " (the scope %s added to the client)" % scope, "PUT",
                       "/clients/%s/default-client-scopes/%s" % (c["id"], sc["id"]), {})
        else:
            api.change("CREATE", label + " (its own mapper)", "POST", own,
                       {"name": claim_name if claim_name not in names_have else claim_name + "-aap", "protocol": "openid-connect",
                        "protocolMapper": "oidc-usermodel-attribute-mapper",
                        "config": dict(tok, **{"user.attribute": attr, "claim.name": claim_name, "jsonType.label": "String"})})
    return c


def ensure_groups(api, s, there, wanted):
    have = {}
    if there:
        for g in api.get("/groups?max=1000") or []:
            have[g.get("name")] = g.get("id")
    for g in wanted:
        if g in have:
            api.same("group %s" % g)
        else:
            api.change("CREATE", "group %s" % g, "POST", "/groups", {"name": g})
    if api.apply and any(g not in have for g in wanted):
        for g in api.get("/groups?max=1000") or []:
            have[g.get("name")] = g.get("id")
    return have


def read_people(path):
    people = []
    with open(path, newline="") as f:
        for n, row in enumerate(csv.reader(f), 1):
            row = [c.strip() for c in row]
            if not row or not row[0] or row[0].startswith("#") or (n == 1 and row[0].lower() == "username"):
                continue
            row += [""] * (6 - len(row))
            people.append({"username": row[0], "email": row[1], "firstName": row[2], "lastName": row[3],
                           "groups": [g.strip() for g in row[4].split(";") if g.strip()], "value": row[5]})
    return people


def ensure_people(api, s, there, groups):
    people = read_people(s["AAP_USERS_FILE"])
    for p in people:
        missing = [g for g in p["groups"] if g not in names(s["AAP_GROUPS"])]
        if missing:
            raise Stop("%s: %s is in a group that AAP_GROUPS does not name: %s" % (s["AAP_USERS_FILE"], p["username"], ", ".join(missing)), 2)
    same, attr = 0, s["X509_USER_ATTRIBUTE"]
    for p in people:
        if attr and not p["value"]:
            raise Stop("%s: %s has no sixth column. With X509_USER_ATTRIBUTE=%s it holds what the card gives for this person."
                       % (s["AAP_USERS_FILE"], p["username"], attr), 2)
        # Keycloak keeps a user name in lower case
        q = "/users?exact=true&username=%s" % urllib.parse.quote(p["username"])
        try:
            found = api.get(q) if there else None
        except Stop as e:
            raise Stop("%s\nThe realm's people cannot be read. A user federation that does not answer does this: check it in "
                       "Keycloak's console (User federation), then run again." % e)
        if not found:
            body = {"username": p["username"], "enabled": True, "emailVerified": True}
            body.update((k, p[k]) for k in ("email", "firstName", "lastName") if p[k])
            if attr and p["value"]:
                body["attributes"] = {attr: [p["value"]]}
            api.change("CREATE", "person %s%s" % (p["username"], (" in " + ", ".join(p["groups"])) if p["groups"] else ""),
                       "POST", "/users", body)
            if api.apply:
                uid = api.get(q)[0]["id"]
                for g in p["groups"]:
                    api.quiet("PUT", "/users/%s/groups/%s" % (uid, groups[g]), {})
            continue
        now = set(g.get("name") for g in api.get("/users/%s/groups" % found[0]["id"]) or [])
        add = [g for g in p["groups"] if g not in now]
        for g in add:
            api.change("CREATE", "person %s: into the group %s" % (p["username"], g), "PUT",
                       "/users/%s/groups/%s" % (found[0]["id"], groups.get(g, "new")), {})
        was = ((found[0].get("attributes") or {}).get(attr) or [""])[0] if attr else ""
        if attr and was != p["value"]:
            add.append(attr)
            body = dict(found[0], attributes=dict(found[0].get("attributes") or {}, **{attr: [p["value"]]}))
            if was:
                api.differ("person %s" % p["username"], [("the attribute %s" % attr, was, p["value"])], "PUT", "/users/%s" % found[0]["id"], body)
            else:
                api.change("CREATE", "person %s: the attribute %s" % (p["username"], attr), "PUT", "/users/%s" % found[0]["id"], body)
        same += 0 if add else 1
    if same:
        api.same("%d of %d people of %s are there, in their groups" % (same, len(people), os.path.basename(s["AAP_USERS_FILE"])))
    return len(people)


STORAGE = "org.keycloak.storage.UserStorageProvider"
LDAP_MAPPER = "org.keycloak.storage.ldap.mappers.LDAPStorageMapper"


def ldap_config(s):
    ad = s["LDAP_VENDOR"] == "ad"

    def period(v):
        return v if int(v) > 0 else "-1"
    return {
        "enabled": "true", "vendor": s["LDAP_VENDOR"], "connectionUrl": s["LDAP_URL"], "usersDn": s["LDAP_USERS_DN"],
        "authType": "simple", "bindDn": s["LDAP_BIND_DN"], "editMode": "READ_ONLY", "importEnabled": "true",
        "syncRegistrations": "false", "usernameLDAPAttribute": ldap_username_attribute(s), "rdnLDAPAttribute": "cn" if ad else "uid",
        "uuidLDAPAttribute": "objectGUID" if ad else ("nsuniqueid" if s["LDAP_VENDOR"] == "rhds" else "entryUUID"),
        "userObjectClasses": "person, organizationalPerson, user" if ad else "inetOrgPerson, organizationalPerson",
        "searchScope": "2", "pagination": "true", "useTruststoreSpi": "always", "trustEmail": "true",
        "startTls": "true" if on(s["LDAP_STARTTLS"]) else "false",
        "connectionPooling": "false" if on(s["LDAP_STARTTLS"]) else "true",
        "customUserSearchFilter": member_filter(s),
        "changedSyncPeriod": period(s["LDAP_SYNC_CHANGED_SECONDS"]), "fullSyncPeriod": period(s["LDAP_SYNC_FULL_SECONDS"]),
    }


def ldap_compared(s):
    """What is compared in a federation that is there: only what the settings say. Without LDAP_GROUP_DN the filter
    is not compared (an update would otherwise take a filter away, and let everybody in)."""
    c = ldap_config(s)
    keys = ["connectionUrl", "usersDn", "bindDn", "vendor", "editMode", "importEnabled", "startTls", "changedSyncPeriod", "fullSyncPeriod"]
    if s["LDAP_GROUP_DN"]:
        keys.append("customUserSearchFilter")
    if "LDAP_USERNAME_ATTRIBUTE" in s["_set"]:
        keys.append("usernameLDAPAttribute")
    return dict((k, c[k]) for k in keys)


def ldap_words(s, name, username=""):
    who = "everybody under %s" % s["LDAP_USERS_DN"]
    if s["LDAP_GROUP_DN"]:
        who = "only the members of %s (%s)" % (s["LDAP_GROUP_DN"], "nested groups count" if nested(s) else "direct members only")

    def every(v):
        return "never" if int(v) <= 0 else "every %s s" % v
    return ["user federation %s: %s%s, READ_ONLY, the user name from %s"
            % (name, s["LDAP_URL"], " with StartTLS" if on(s["LDAP_STARTTLS"]) else "", username or ldap_username_attribute(s)),
            "user federation %s: lets in %s" % (name, who),
            "user federation %s: the changed people are read %s, all of them %s"
            % (name, every(s["LDAP_SYNC_CHANGED_SECONDS"]), every(s["LDAP_SYNC_FULL_SECONDS"]))]


def team_config(s):
    dns = team_groups(s)
    ad = s["LDAP_VENDOR"] == "ad"
    if ad:
        one = ["(distinguishedName=%s)" % filter_value(d) for d in dns]
    else:
        one = ["(%s)" % filter_value(split_dn(d)[0]) for d in dns]
    return {
        "groups.dn": common_base(dns), "groups.ldap.filter": one[0] if len(one) == 1 else "(|%s)" % "".join(one),
        "group.name.ldap.attribute": "cn", "group.object.classes": "group" if ad else "groupOfNames",
        "membership.ldap.attribute": "member", "membership.attribute.type": "DN", "membership.user.ldap.attribute": "cn" if ad else "uid",
        "mode": "READ_ONLY", "preserve.group.inheritance": "false", "ignore.missing.groups": "true",
        "user.roles.retrieve.strategy": "LOAD_GROUPS_BY_MEMBER_ATTRIBUTE" + ("_RECURSIVELY" if nested(s) else ""),
        "memberof.ldap.attribute": "memberOf", "drop.non.existing.groups.during.sync": "false", "groups.path": "/",
    }


def under(dn, base):
    a, b = [p.lower().replace(" ", "") for p in split_dn(dn)], [p.lower().replace(" ", "") for p in split_dn(base)]
    return len(a) > len(b) and a[-len(b):] == b


def find_ldap(api, s, there):
    """The realm's LDAP federation the settings mean, and its mappers: the one named LDAP_NAME; else, when the settings
    do not name one, the only one there is, or the one with LDAP_URL's address. (None, []) when it is to be made."""
    if not there:
        return None, []
    feds = [c for c in (api.get("/components?type=%s" % STORAGE) or []) if c.get("providerId") == "ldap"]
    named = [c for c in feds if c.get("name") == s["LDAP_NAME"]]
    if named:
        c = named[0]
    elif "LDAP_NAME" in s["_set"] or not feds:
        return None, []
    elif len(feds) == 1:
        c = feds[0]
    else:
        same = [c for c in feds if flat(c.get("config")).get("connectionUrl", "").lower() == s["LDAP_URL"].lower()]
        if len(same) != 1:
            raise Stop("The realm has %d LDAP federations (%s). Set LDAP_NAME to the one AAP's people come from."
                       % (len(feds), ", ".join(str(c.get("name")) for c in feds)), 2)
        c = same[0]
    if c.get("name") != s["LDAP_NAME"]:
        api.found("user federation %s: the realm's LDAP federation. It is used under its own name" % c.get("name"))
    return c, api.get("/components?parent=%s&type=%s" % (c["id"], LDAP_MAPPER)) or []


def card_attribute(api, s, mappers):
    """The federation's mapper that reads the card's value (LDAP_CARD_ATTRIBUTE) into a user attribute. A mapper that
    is there under another attribute name is taken as it is when the settings do not name X509_USER_ATTRIBUTE."""
    ldap_attr = s["LDAP_CARD_ATTRIBUTE"].lower()
    if not ldap_attr:
        return None
    of = [m for m in mappers if m.get("providerId") == "user-attribute-ldap-mapper"
          and flat(m.get("config")).get("ldap.attribute", "").lower() == ldap_attr
          and flat(m.get("config")).get("user.model.attribute", "").lower() not in ("", "username", "email", "firstname", "lastname")]
    exact = [m for m in of if flat(m.get("config")).get("user.model.attribute") == s["X509_USER_ATTRIBUTE"]]
    if exact:
        return exact[0]
    if of and "X509_USER_ATTRIBUTE" not in s["_set"]:
        attr = flat(of[0].get("config"))["user.model.attribute"]
        api.found("the mapper %s reads %s into the user attribute %s: the card is looked up in %s"
                  % (of[0].get("name"), s["LDAP_CARD_ATTRIBUTE"], attr, attr))
        s["X509_USER_ATTRIBUTE"] = attr
        return of[0]
    return None


def ensure_ldap(api, s, fed, mappers, card, realm_id):
    """The read-only federation, its mappers, and (apply) Keycloak's own try of the bind. Returns the component's id."""
    ldap_cert(api, s)
    if fed is None:
        name = s["LDAP_NAME"]
        words = ldap_words(s, name)
        want = ldap_config(s)
        body = {"name": name, "providerId": "ldap", "providerType": STORAGE, "config": dict((k, [v]) for k, v in want.items())}
        if realm_id:
            body["parentId"] = realm_id
        if api.apply:
            body["config"]["bindCredential"] = [secret_from(s["LDAP_BIND_PASSWORD_FILE"], "Password of %s" % s["LDAP_BIND_DN"])]
        made = api.change("CREATE", words[0], "POST", "/components", body)
        for w in words[1:]:
            api.out("        %s" % w.split(": ", 1)[1])
        if made:
            fed = [c for c in (api.get("/components?type=%s" % STORAGE) or []) if c.get("name") == name][0]
            mappers = api.get("/components?parent=%s&type=%s" % (fed["id"], LDAP_MAPPER)) or []
    else:
        name = fed.get("name")
        have = flat(fed.get("config"))
        for k, v in (("startTls", "false"), ("changedSyncPeriod", "-1"), ("fullSyncPeriod", "-1")):
            have[k] = have.get(k) or v                      # what Keycloak means when the field is not there
        want = ldap_compared(s)
        d = rows(have, want)
        words = ldap_words(s, name, have.get("usernameLDAPAttribute", ""))
        if d:
            cfg = dict(fed.get("config") or {})
            cfg.update((k, [v]) for k, v in differs(have, want).items())
            api.differ("user federation %s" % name, d, "PUT", "/components/%s" % fed["id"], dict(fed, config=cfg))
        else:
            for w in words:
                api.same(w)
        if not s["LDAP_GROUP_DN"]:
            f = have.get("customUserSearchFilter", "")
            api.out("note    user federation %s: %s (LDAP_GROUP_DN is not set: not compared)"
                    % (name, "its filter is %s" % show(f) if f else "it has NO filter: everybody under %s can log in" % have.get("usersDn", "")))
    cid = fed["id"] if fed else ""
    changed_groups = None
    # the card's attribute
    if s["LDAP_CARD_ATTRIBUTE"] and s["X509_USER_ATTRIBUTE"]:
        label = ("user federation %s: the LDAP attribute %s is read into the user attribute %s (what the card is looked up in)"
                 % (name, s["LDAP_CARD_ATTRIBUTE"], s["X509_USER_ATTRIBUTE"]))
        if card is None:
            taken = set(m.get("name") for m in mappers)
            mname = s["X509_USER_ATTRIBUTE"] if s["X509_USER_ATTRIBUTE"] not in taken else s["X509_USER_ATTRIBUTE"] + "-card"
            api.change("CREATE", label, "POST", "/components",
                       {"name": mname, "providerId": "user-attribute-ldap-mapper", "providerType": LDAP_MAPPER, "parentId": cid or "new",
                        "config": {"ldap.attribute": [s["LDAP_CARD_ATTRIBUTE"]], "user.model.attribute": [s["X509_USER_ATTRIBUTE"]],
                                   "read.only": ["true"], "always.read.value.from.ldap": ["true"], "is.mandatory.in.ldap": ["false"]}})
        else:
            api.same(label + ", by the mapper %s" % card.get("name"))
    # the groups that become AAP's teams
    gm = [m for m in mappers if m.get("providerId") == "group-ldap-mapper"]
    named = [m for m in gm if m.get("name") == s["LDAP_TEAM_MAPPER"]]
    dns = team_groups(s)
    if not dns:
        for m in gm:
            c = flat(m.get("config"))
            api.found("user federation %s: the group mapper %s brings in the groups under %s%s (LDAP_TEAM_GROUPS is not set: left as it is)"
                      % (name, m.get("name"), c.get("groups.dn", "?"), ", filter %s" % c["groups.ldap.filter"] if c.get("groups.ldap.filter") else ""))
    else:
        short = ", ".join(split_dn(d)[0].split("=", 1)[1] for d in dns)
        want = team_config(s)
        if named:
            g = named[0]
        elif "LDAP_TEAM_MAPPER" in s["_set"] or not gm:
            g = None
        elif len(gm) == 1:
            g = gm[0]
        else:
            raise Stop("The federation %s has %d group mappers (%s). Set LDAP_TEAM_MAPPER to the one for AAP's teams."
                       % (name, len(gm), ", ".join(str(m.get("name")) for m in gm)), 2)
        if g is None:
            api.change("CREATE", "user federation %s: the groups %s become Keycloak groups, with their members (the mapper %s, read-only%s)"
                       % (name, short, s["LDAP_TEAM_MAPPER"], ", nested members count" if nested(s) else ""), "POST", "/components",
                       {"name": s["LDAP_TEAM_MAPPER"], "providerId": "group-ldap-mapper", "providerType": LDAP_MAPPER,
                        "parentId": cid or "new", "config": dict((k, [v]) for k, v in want.items())})
            changed_groups = s["LDAP_TEAM_MAPPER"]
        else:
            have = flat(g.get("config"))
            d = []
            if not all(under(dn, have.get("groups.dn", "")) for dn in dns):
                d.append(("groups.dn", have.get("groups.dn"), want["groups.dn"]))
            if have.get("groups.ldap.filter") and have["groups.ldap.filter"] != want["groups.ldap.filter"]:
                d.append(("groups.ldap.filter", have["groups.ldap.filter"], want["groups.ldap.filter"]))
            for k in ("mode", "user.roles.retrieve.strategy"):
                if have.get(k) != want[k]:
                    d.append((k, have.get(k), want[k]))
            label = "user federation %s: the group mapper %s" % (name, g.get("name"))
            if d:
                cfg = dict(g.get("config") or {})
                cfg.update((k, [w]) for k, _, w in d)
                if api.differ(label, d, "PUT", "/components/%s" % g["id"], dict(g, config=cfg)):
                    changed_groups = g.get("name")
            else:
                api.same("%s brings in the groups %s, with their members%s"
                         % (label, short, "" if have.get("groups.ldap.filter") else " (and every other group under %s)" % have.get("groups.dn")))
    if api.apply and cid:
        ok = ldap_answers(api, s, cid)
        if ok and changed_groups:
            sync_groups(api, cid, changed_groups)
    return cid


def sync_groups(api, cid, mapper):
    m = [x for x in (api.get("/components?parent=%s&type=%s" % (cid, LDAP_MAPPER)) or []) if x.get("name") == mapper]
    if m:
        code, data, text = api._send("POST", api.base + "/user-storage/%s/mappers/%s/sync?direction=fedToKeycloak" % (cid, m[0]["id"]), {})
        api.out("note    the groups of the mapper %s were read from the directory%s" % (mapper, sync_words(code, data, text)))


def sync_words(code, data, text):
    if code not in (200, 201, 204):
        return ": Keycloak answered HTTP %s %s" % (code, " ".join(text.split())[:200])
    if not isinstance(data, dict):
        try:
            data = json.loads(text)
        except ValueError:
            return ""
    if not isinstance(data, dict):
        return ""
    return ": %s added, %s updated, %s removed, %s failed" % tuple(data.get(k, 0) for k in ("added", "updated", "removed", "failed"))


def ldap_cert(api, s):
    """LDAP_CA_FILE: the LDAP server's certificate, checked from this machine. It only reads; one line."""
    if not s["LDAP_CA_FILE"]:
        return
    m = re.match(r"^(ldaps?)://(\[[^\]]+\]|[^:/]+)(?::(\d+))?", s["LDAP_URL"])
    host, port = m.group(2).strip("[]"), int(m.group(3) or (636 if m.group(1) == "ldaps" else 389))
    if m.group(1) != "ldaps":
        api.out("note    LDAP_CA_FILE: not checked from here with StartTLS. Keycloak's own try of the bind (--apply) tells.")
        return
    try:
        ctx = ssl.create_default_context(cafile=s["LDAP_CA_FILE"])
        with socket.create_connection((host, port), timeout=float(s["KEYCLOAK_TIMEOUT"])) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                pass
        api.out("note    the certificate of %s:%d is signed by LDAP_CA_FILE and is for that name (checked from this machine)" % (host, port))
    except ssl.SSLError as e:
        api.out("WARNING the certificate of %s:%d does not pass with LDAP_CA_FILE: %s" % (host, port, getattr(e, "verify_message", "") or e))
        api.out("        Keycloak will refuse it too. Give the CA chain that signed the LDAP server's certificate.")
    except (OSError, ValueError) as e:
        api.out("note    %s:%d is not reached from this machine (%s): its certificate was not checked here" % (host, port, e))


def ldap_answers(api, s, component):
    """Asks Keycloak to try the bind with what it holds. It changes nothing; the answer is one line. True: it works."""
    have = flat(([c for c in (api.get("/components?type=%s" % STORAGE) or []) if c.get("id") == component] or [{}])[0].get("config"))
    url = have.get("connectionUrl") or s["LDAP_URL"]
    code, _, text = api._send("POST", api.base + "/testLDAPConnection", {
        "action": "testAuthentication", "componentId": component, "connectionUrl": url, "bindDn": have.get("bindDn") or s["LDAP_BIND_DN"],
        "bindCredential": "**********", "useTruststoreSpi": have.get("useTruststoreSpi") or "always", "authType": "simple",
        "connectionTimeout": "5000", "startTls": have.get("startTls") or "false"})
    if code in (200, 204):
        api.out("note    Keycloak reached %s and the bind account was accepted" % url)
        return True
    api.out("WARNING Keycloak could not bind to %s: its people cannot log in, and reading the realm's people fails, until it can." % url)
    api.out("        Check the address, the bind account, and that Keycloak's trust store (--truststore-paths) holds the CA")
    api.out("        chain of the LDAP server's certificate.")
    return False


def ldap_people(api, cid):
    """How many of the realm's people came from the federation."""
    n, first = 0, 0
    while first < 50000:
        page = api.get("/users?briefRepresentation=false&first=%d&max=500" % first) or []
        n += sum(1 for u in page if u.get("federationLink") == cid)
        if len(page) < 500:
            break
        first += 500
    return n


def ldap_sync(api, s, cid):
    """--sync: one full sync now. Keycloak reads the directory; nothing is written to it."""
    if not api.apply or not cid:
        api.out("SYNC    with --apply: one full sync of the directory, and its result")
        return
    code, data, text = api._send("POST", api.base + "/user-storage/%s/sync?action=triggerFullSync" % cid, {})
    if code not in (200, 201, 204):
        raise Stop("The full sync failed: HTTP %s %s" % (code, " ".join(text.split())[:300]))
    api.out("SYNC    a full sync of the directory%s" % (sync_words(code, data, text) or ": done"))
    for m in api.get("/components?parent=%s&type=%s" % (cid, LDAP_MAPPER)) or []:
        if m.get("providerId") == "group-ldap-mapper":
            sync_groups(api, cid, m.get("name"))
    api.out("        %d of the realm's people come from the directory now" % ldap_people(api, cid))


# ---- --compare: this realm against an export of another one -----------------------------------------------------
MASK = "**********"
STOCK_CLIENTS = ("account", "account-console", "admin-cli", "broker", "realm-management", "security-admin-console")
UNSET = ("false", "-1", "")                # a field one side does not have is the same as one of these on the other
NOT_COMPARED = ("access", "attributes.client.secret.creation.time", "id", "realm", "secret", "containerId", "parentId", "internalId", "lastSync", "bindCredential", "keycloakVersion",
                "registrationAccessToken", "notBefore")


def plain(v):
    """A value two sides can be compared by: lists sorted, the rest as text. None: not comparable (a structure)."""
    if isinstance(v, (list, tuple)):
        if any(isinstance(x, (dict, list)) for x in v):
            return None
        return ", ".join(sorted(str(x) for x in v))
    if isinstance(v, dict):
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def scalars(d, into, prefix=""):
    """The comparable fields of one object, one level of {..} deep."""
    for k, v in (d or {}).items():
        if k in NOT_COMPARED or k == MARK or (prefix + k) in NOT_COMPARED:
            continue
        if isinstance(v, dict) and not prefix:
            scalars(v, into, k + ".")
        else:
            p = plain(v)
            if p is not None and p != MASK:
                into[prefix + k] = p
    return into


def view_mappers(view, where, mappers, kind_key):
    for m in mappers or []:
        w = "%s: the mapper %s" % (where, m.get("name"))
        view[w] = scalars({"kind": m.get(kind_key)}, {})
        scalars(flat(m.get("config")), view[w])


def view_clients(view, clients):
    for c in clients or []:
        if c.get("clientId") in STOCK_CLIENTS or str(c.get("clientId", "")).endswith("-realm"):
            continue
        w = "client %s" % c.get("clientId")
        view[w] = scalars(dict((k, v) for k, v in c.items() if k not in ("protocolMappers", "clientId")), {})
        view_mappers(view, w, c.get("protocolMappers"), "protocolMapper")


def view_export(doc):
    """What an export (a partial export, or a full realm export) says, as {thing: {field: value}} and the flow's steps."""
    view = {"realm settings": scalars(dict((k, v) for k, v in doc.items() if not isinstance(v, list) or k in (
        "requiredCredentials", "enabledEventTypes", "supportedLocales", "eventsListeners")), {})}
    view_clients(view, doc.get("clients"))
    for c in (doc.get("components") or {}).get(STORAGE) or []:
        w = "user federation %s" % c.get("name")
        view[w] = scalars({"kind": c.get("providerId")}, {})
        scalars(flat(c.get("config")), view[w])
        view_mappers(view, w, (c.get("subComponents") or {}).get(LDAP_MAPPER), "providerId")
    for g in doc.get("groups") or []:
        view["group %s" % g.get("name")] = {}
    flows = dict((f.get("alias"), f) for f in doc.get("authenticationFlows") or [])
    configs = dict((c.get("alias"), c.get("config") or {}) for c in doc.get("authenticatorConfig") or [])
    steps = []

    def walk(alias, level):
        for e in sorted((flows.get(alias) or {}).get("authenticationExecutions") or [], key=lambda e: e.get("priority", 0)):
            if e.get("authenticatorFlow") or e.get("autheticatorFlow"):
                steps.append("%s%s %s" % ("  " * level, "(a sub-flow)", e.get("requirement")))
                walk(e.get("flowAlias"), level + 1)
            else:
                steps.append("%s%s %s" % ("  " * level, e.get("authenticator"), e.get("requirement")))
                if e.get("authenticator") == X509 and e.get("authenticatorConfig") in configs:
                    view["the card step"] = scalars(configs[e["authenticatorConfig"]], {})
    walk(doc.get("browserFlow") or "browser", 0)
    return view, steps


def view_live(api):
    realm = api.get("")
    if realm is None:
        raise Stop("The realm %s is not there: there is nothing to compare. Make it first (--apply)." % api.realm, 2)
    view = {"realm settings": scalars(realm, {})}
    view_clients(view, api.get("/clients?max=1000"))
    for c in api.get("/components?type=%s" % STORAGE) or []:
        w = "user federation %s" % c.get("name")
        view[w] = scalars({"kind": c.get("providerId")}, {})
        scalars(flat(c.get("config")), view[w])
        view_mappers(view, w, api.get("/components?parent=%s&type=%s" % (c["id"], LDAP_MAPPER)), "providerId")
    for g in api.get("/groups?max=1000") or []:
        view["group %s" % g.get("name")] = {}
    steps = []
    for e in api.get("/authentication/flows/%s/executions" % urllib.parse.quote(realm.get("browserFlow") or "browser")) or []:
        level = int(e.get("level") or 0)
        if e.get("authenticationFlow"):
            steps.append("%s%s %s" % ("  " * level, "(a sub-flow)", e.get("requirement")))
        else:
            steps.append("%s%s %s" % ("  " * level, e.get("providerId"), e.get("requirement")))
            if e.get("providerId") == X509 and e.get("authenticationConfig"):
                view["the card step"] = scalars((api.get("/authentication/config/%s" % e["authenticationConfig"]) or {}).get("config"), {})
    return view, steps


def pair_up(here, there):
    """Things of one kind that carry another name on each side are one thing when each side has exactly one of them."""
    renamed = {}
    for kind in ("client ", "user federation "):
        a = [k for k in here if k.startswith(kind) and ": " not in k and k not in there]
        b = [k for k in there if k.startswith(kind) and ": " not in k and k not in here]
        if len(a) == 1 and len(b) == 1:
            renamed[b[0]] = a[0]
    out = {}
    for k, v in there.items():
        head = k.split(": ", 1)[0]
        out[renamed[head] + k[len(head):] if head in renamed else k] = v
    return out, renamed


def compare(api, s, path, out):
    """Prints how this realm differs from an export. It reads only: nothing is changed, nothing is imported."""
    try:
        with open(path) as f:
            doc = json.load(f)
    except (IOError, OSError, ValueError) as e:
        raise Stop("--compare: %s cannot be read as a realm export (JSON): %s" % (path, e), 2)
    if not isinstance(doc, dict) or "realm" not in doc:
        raise Stop("--compare: %s is not a realm export: it has no \"realm\" in it. In Keycloak's console: Realm settings > "
                   "Action > Partial export, with groups, roles and clients." % path, 2)
    theirs, their_steps = view_export(doc)
    mine, my_steps = view_live(api)
    theirs, renamed = pair_up(mine, theirs)
    out("COMPARE - the realm %s here with %s (the realm %s%s). Nothing is changed, nothing is imported."
        % (api.realm, os.path.basename(path), doc.get("realm"), ", Keycloak %s" % doc["keycloakVersion"] if doc.get("keycloakVersion") else ""))
    out("")
    for there, here in sorted(renamed.items()):
        out("note    %s of the export is taken as %s here: the only one of its kind on each side" % (there, here))
    n = same = 0
    for thing in sorted(set(mine) | set(theirs)):
        if thing not in theirs:
            n += 1
            out("only here           %s" % thing)
        elif thing not in mine:
            n += 1
            out("only in the export  %s" % thing)
        else:
            a, b = mine[thing], theirs[thing]
            lines = []
            for k in sorted(set(a) | set(b)):
                if thing == "realm settings" and (k not in a or k not in b):
                    continue                      # a field one Keycloak version has and the other has not
                if a.get(k, "") != b.get(k, "") and not ((k not in a or k not in b) and (a.get(k) or b.get(k)) in UNSET):
                    lines.append("differs %s: %s is %s here, the export says %s" % (thing, k, show(a.get(k) if k in a else None),
                                                                                   show(b.get(k) if k in b else None)))
            if lines:
                n += len(lines)
                for line in lines:
                    out(line)
            else:
                same += 1
    if my_steps != their_steps:
        n += 1
        out("differs the browser flow: its steps here, then the export's")
        for line in my_steps:
            out("          here    %s" % line)
        for line in their_steps:
            out("          export  %s" % line)
    else:
        same += 1
    out("")
    out("%d difference%s; %d thing%s the same on both sides." % (n, "" if n == 1 else "s", same, "" if same == 1 else "s"))
    out("Compared: the realm's settings, the browser flow's steps and its card step, the clients (not Keycloak's own), their")
    out("  mappers, the user federations, their mappers, the top groups. Not compared: people, roles, client scopes, ids, and")
    out("  every secret (an export masks them: %s)." % MASK)
    out("Addresses, DNs and host names differ between two networks on purpose: those lines are what this enclave's")
    out("  settings file holds. The other lines are what to line up.")


def pick_door(s, via, password, realm, out):
    """rest when the admin API answers at KEYCLOAK_ADMIN_URL; else kcadm when a Keycloak container is here."""
    why = ""
    if via in ("auto", "rest"):
        door = Rest(s, password)
        try:
            door.login()
            code, _, text = door.call("GET", "/admin/realms/" + realm)
            if code in (200, 404):
                return door
            why = "the admin API at %s answered HTTP %s%s" % (door.url, code, hint_for(code, text))
        except Stop as e:
            why = str(e)
        if via == "rest":
            raise Stop(why)
    try:
        door = Kcadm(s, password)
        door.login()
        return door
    except Stop as e:
        if via == "kcadm":
            raise
        raise Stop("No way to Keycloak's admin API.\n  rest:  %s\n  kcadm: %s" % (why, e))


def run(argv, env, out):
    apply = adopt = sync = update = False
    via, settings, secret_file, export = "auto", "", "", ""
    args = list(argv)
    while args:
        a = args.pop(0)
        if a in ("-h", "--help"):
            out(__doc__.replace("@SETTINGS@\n", settings_help()).rstrip())
            return 0
        elif a == "--apply":
            apply = True
        elif a == "--dry-run":
            apply = False
        elif a == "--adopt":
            adopt = True
        elif a == "--sync":
            sync = True
        elif a == "--update-existing":
            update = True
        elif a in ("--via", "--settings", "--secret-file", "--compare"):
            if not args:
                raise Stop("%s needs a value (see --help)" % a, 2)
            v = args.pop(0)
            if a == "--via":
                if v not in ("auto", "rest", "kcadm"):
                    raise Stop("--via must be auto, rest or kcadm", 2)
                via = v
            elif a == "--settings":
                settings = v
            elif a == "--compare":
                export = v
            else:
                secret_file = v
        else:
            raise Stop("Not an option: %s (see --help). A password is never an argument." % a, 2)
    s = read_settings(settings, env)
    check_settings(s)
    if sync and not s["LDAP_URL"]:
        raise Stop("--sync needs LDAP_URL: there is no directory to read (see --help)", 2)
    if export and (apply or adopt or sync or update or secret_file):
        raise Stop("--compare only reads and prints: it goes with --settings and --via, and with nothing else (see --help)", 2)
    realm = s["KEYCLOAK_REALM"]
    password = secret_from(s["KEYCLOAK_ADMIN_PASSWORD_FILE"], "Password of the Keycloak admin %s" % s["KEYCLOAK_ADMIN_USER"])
    door = pick_door(s, via, password, realm, out)
    api = Api(door, realm, apply, out, update)
    try:
        if export:
            compare(api, s, export, out)
            out("Requests: %d, all reads of /admin/realms/%s; to another realm: 0." % (len(api.sent), realm))
            return 0
        out("%s - the realm %s, through %s (%s)" % ("APPLY" if apply else "DRY RUN: nothing is changed", realm, door.name, door.where))
        out("")
        there, fresh = ensure_realm(api, s, adopt)
        fed, mappers, card = None, [], None
        if s["LDAP_URL"]:
            fed, mappers = find_ldap(api, s, there)
            card = card_attribute(api, s, mappers)
        flow, flows = card_flow(api, s, there)
        ensure_flow(api, s, there, flow, flows)
        bind_flow(api, s, there, fresh, flow)
        if s["X509_USER_ATTRIBUTE"]:
            ensure_attribute(api, s, there)
        client = ensure_client(api, s, there)
        groups = ensure_groups(api, s, there, names(s["AAP_GROUPS"]))
        ldap = ensure_ldap(api, s, fed, mappers, card, (api.get("") or {}).get("id") if there else "") if s["LDAP_URL"] else ""
        people = ensure_people(api, s, there, groups) if s["AAP_USERS_FILE"] else 0
        if secret_file:
            if not apply or client is None:
                out("note    the client's secret is written to %s only with --apply" % secret_file)
            else:
                sec = (api.get("/clients/%s/client-secret" % client["id"]) or {}).get("value") or ""
                fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(sec + "\n")
                os.chmod(secret_file, 0o600)
                out("note    the client's secret is in %s (mode 600): paste it in AAP's \"OIDC Secret\", then delete the file" % secret_file)
        if sync:
            ldap_sync(api, s, ldap)
        out("")
        if s["LDAP_URL"] and s["LDAP_GROUP_DN"]:
            out("People: they come from the directory, the members of %s. Nobody is made here."
                % split_dn(s["LDAP_GROUP_DN"])[0].split("=", 1)[1])
            out("  A member is taken in at the first card login, or by a sync. Out of the group: refused at the next login.")
        elif s["LDAP_URL"]:
            out("People: they come from the directory. Nobody is made here.")
            out("WARNING LDAP_GROUP_DN is not set: the settings name no group. Whoever the federation's own filter lets in")
            out("        can log in with a card - with no filter, everybody under LDAP_USERS_DN. Name the group whose")
            out("        members may: LDAP_GROUP_DN.")
        if not s["LDAP_URL"] and not people:
            out("People: none are made. Stock Keycloak does not make a user at a first card login: a person must be in the")
            out("  realm first. Name a list (AAP_USERS_FILE), add them in Keycloak's console, or set a federation (LDAP_URL).")
        out("AAP's authenticator (Generic OIDC) - its fields:")
        for k, v in (("OIDC Provider URL", "%s/realms/%s" % (s["KEYCLOAK_URL"], realm)), ("OIDC Key", s["KEYCLOAK_CLIENT_ID"]),
                     ("OIDC Secret", "the client's secret (--secret-file)"), ("OIDC JWT Algorithm(s)", "RS256"),
                     ("Username Key", "preferred_username"), ("Groups Claim", s["AAP_GROUPS_CLAIM"]), ("the callback, in Keycloak", redirect_uri(s))):
            out("  %-26s %s" % (k, v))
        other = [m + " " + p for m, p in api.sent if not allowed(m, p, realm)]
        if api.differences:
            out("%d field%s of things that are there differ%s from the settings (the \"differs\" lines). %s left as %s."
                % (api.differences, "" if api.differences == 1 else "s", "s" if api.differences == 1 else "",
                   "It was" if api.differences == 1 else "They were", "it is" if api.differences == 1 else "they are"))
            out("  To change them to what the settings say, and nothing else: add --update-existing. Or change the settings.")
        if api.changes == 0:
            out("Nothing to change: %s" % ("nothing is missing in the realm %s." % realm if api.differences
                                           else "the realm %s is as the settings say." % realm))
        elif apply:
            out("%d change%s made in the realm %s." % (api.changes, "" if api.changes == 1 else "s", realm))
        else:
            out("%d change%s would be made. Nothing was changed (dry run). Run again with --apply to make them."
                % (api.changes, "" if api.changes == 1 else "s"))
        out("Requests: %d, all to /admin/realms/%s%s; to another realm: %d."
            % (len(api.sent), realm, " (and the one that creates the realm)" if ("POST", "/admin/realms") in api.sent else "", len(other)))
        return 0
    finally:
        door.close()


def main():
    try:
        return run(sys.argv[1:], os.environ, print)
    except Stop as e:
        sys.stderr.write("%s\n" % e)
        return e.code
    except KeyboardInterrupt:
        sys.stderr.write("Stopped.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
