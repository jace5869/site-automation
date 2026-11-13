#!/usr/bin/env python3
"""Tests for tools/aap-keycloak-realm.py (1.51.0): AAP's own Keycloak realm, made by a script. It is run for real
against a fake Keycloak admin API on localhost that holds two realms: a dry run changes nothing, --apply makes the
realm, a second --apply changes nothing, and the other realm is the same before and after. Every request the
script sent is written down by the fake: all of them are the realm's own. The kcadm way runs through a stand-in
container runtime. python3 tests/test_aap_keycloak_realm.py"""
import copy
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
SCRIPT = os.path.join(REPO, "tools", "aap-keycloak-realm.py")
EXAMPLE = os.path.join(REPO, "tools", "aap-keycloak.env.example")
DOC = os.path.join(REPO, "docs", "AAP_KEYCLOAK.md")
PASSWORD = "pw-7d1e55c09b"
LDAP_PASSWORD = "bind-41c2aa90"
CLIENT_SECRET = "sec-90ab12cd34ef"
TOKEN = "admin-token-1"
MARK = "site-automation-aap-realm"
X509 = "auth-x509-client-username-form"
ACCESS = "CN=AAP-Access,OU=Groups,DC=corp,DC=example,DC=mil"
OPERATORS = "CN=AAP-Operators,OU=Groups,DC=corp,DC=example,DC=mil"
ADMINS = "CN=AAP-Admins,OU=Groups,DC=corp,DC=example,DC=mil"
STORAGE = "org.keycloak.storage.UserStorageProvider"
LDAP_MAPPER = "org.keycloak.storage.ldap.mappers.LDAPStorageMapper"


def read(p):
    with open(p) as f:
        return f.read()


def load():
    spec = importlib.util.spec_from_file_location("aap_keycloak_realm", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def new_realm(name, attributes=None):
    """What Keycloak gives a new realm: its built-in flows and client scopes."""
    return {
        "rep": {"id": "id-" + name, "realm": name, "enabled": True, "browserFlow": "browser",
                "registrationAllowed": False, "resetPasswordAllowed": False, "rememberMe": False,
                "attributes": dict(attributes or {})},
        "flows": {"browser": [
            {"id": "b1", "level": 0, "providerId": "auth-cookie", "requirement": "ALTERNATIVE", "displayName": "Cookie"},
            {"id": "b2", "level": 0, "providerId": "identity-provider-redirector", "requirement": "ALTERNATIVE",
             "displayName": "Identity Provider Redirector"},
            {"id": "b3", "level": 0, "authenticationFlow": True, "requirement": "ALTERNATIVE", "displayName": "forms"},
            {"id": "b4", "level": 1, "providerId": "auth-username-password-form", "requirement": "REQUIRED",
             "displayName": "Username Password Form"}]},
        "configs": {}, "profile": {"attributes": [{"name": "username"}, {"name": "email"}]},
        "clients": [], "mappers": {}, "client_default_scopes": {},
        "scopes": [
            {"id": "sc-profile", "name": "profile", "protocolMappers": [
                {"name": "username", "config": {"claim.name": "preferred_username"}}]},
            {"id": "sc-email", "name": "email", "protocolMappers": [{"name": "email", "config": {"claim.name": "email"}}]}],
        "groups": [], "users": [], "user_groups": {}, "components": [],
    }


def hand_realm(name="aap"):
    """A realm a person made in Keycloak's console: no mark, other names, some fields not as the settings say."""
    r = new_realm(name)
    r["rep"].update(displayName="AAP (by hand)", browserFlow="aap browser",
                    browserSecurityHeaders={"contentSecurityPolicy": "frame-src 'self'", "xFrameOptions": "SAMEORIGIN"})
    r["profile"]["unmanagedAttributePolicy"] = "ADMIN_EDIT"
    r["flows"]["aap browser"] = [
        {"id": "h1", "level": 0, "providerId": "auth-cookie", "requirement": "ALTERNATIVE", "displayName": "Cookie"},
        {"id": "h2", "level": 0, "providerId": X509, "requirement": "ALTERNATIVE", "authenticationConfig": "h-cfg",
         "displayName": "X509/Validate Username Form"},
        {"id": "h3", "level": 0, "authenticationFlow": True, "requirement": "ALTERNATIVE", "displayName": "aap browser forms"},
        {"id": "h4", "level": 1, "providerId": "auth-username-password-form", "requirement": "REQUIRED",
         "displayName": "Username Password Form"}]
    r["configs"]["h-cfg"] = {"id": "h-cfg", "alias": "cac", "config": {
        "x509-cert-auth.mapping-source-selection": "Subject's Alternative Name otherName (UPN)",
        "x509-cert-auth.mapper-selection": "Custom Attribute Mapper",
        "x509-cert-auth.mapper-selection.user-attribute-name": "aap-upn",
        "x509-cert-auth.confirmation-page-disallowed": "false", "x509-cert-auth.timestamp-validation-enabled": "true",
        "x509-cert-auth.ocsp-checking-enabled": "false", "x509-cert-auth.crl-checking-enabled": "false",
        "x509-cert-auth.canonical-dn-enabled": "false", "x509-cert-auth.serialnumber-hex-enabled": "false",
        "x509-cert-auth.regular-expression": "(.*?)(?:$)"}}
    r["clients"].append({"id": "h-client", "clientId": "aap", "name": "AAP", "protocol": "openid-connect",
                         "publicClient": False, "standardFlowEnabled": True, "directAccessGrantsEnabled": True,
                         "enabled": True, "redirectUris": ["https://aap.yoursite.mil/api/gateway/social/complete/oidc/"]})
    r["mappers"]["h-client"] = []
    r["client_default_scopes"]["h-client"] = ["profile", "email"]
    r["groups"].append({"id": "h-group", "name": "keep-me"})
    r["users"].append({"id": "h-user", "username": "local.admin", "enabled": True, "attributes": {"note": ["by hand"]}})

    def lists(d):
        return {k: [v] for k, v in d.items()}
    people = "DC=corp,DC=example,DC=mil"
    r["components"] += [
        {"id": "h-fed", "name": "corp-ad", "providerId": "ldap", "providerType": STORAGE, "parentId": "id-" + name,
         "config": lists({"vendor": "ad", "connectionUrl": "ldaps://dc01.corp.example.mil:636", "usersDn": "OU=People," + people,
                          "bindDn": "CN=svc-keycloak,OU=Service," + people, "bindCredential": "**********",
                          "authType": "simple", "editMode": "READ_ONLY", "importEnabled": "true",
                          "usernameLDAPAttribute": "sAMAccountName", "rdnLDAPAttribute": "cn", "uuidLDAPAttribute": "objectGUID",
                          "userObjectClasses": "person, organizationalPerson, user", "searchScope": "2", "pagination": "true",
                          "enabled": "true", "useTruststoreSpi": "always", "connectionPooling": "true", "trustEmail": "false",
                          "syncRegistrations": "false"})},
        {"id": "h-upn", "name": "aap-upn", "providerId": "user-attribute-ldap-mapper", "providerType": LDAP_MAPPER,
         "parentId": "h-fed", "config": lists({"ldap.attribute": "userPrincipalName", "user.model.attribute": "aap-upn",
                                               "read.only": "true", "always.read.value.from.ldap": "true",
                                               "is.mandatory.in.ldap": "false"})},
        {"id": "h-emp", "name": "employee_ID", "providerId": "user-attribute-ldap-mapper", "providerType": LDAP_MAPPER,
         "parentId": "h-fed", "config": lists({"ldap.attribute": "employeeID", "user.model.attribute": "employee_ID",
                                               "read.only": "true", "always.read.value.from.ldap": "false",
                                               "is.mandatory.in.ldap": "false"})},
        {"id": "h-msad", "name": "MSAD account controls", "providerId": "msad-user-account-control-mapper",
         "providerType": LDAP_MAPPER, "parentId": "h-fed", "config": {}},
        {"id": "h-groups", "name": "aap-groups", "providerId": "group-ldap-mapper", "providerType": LDAP_MAPPER,
         "parentId": "h-fed", "config": lists({
             "groups.dn": "OU=Groups," + people, "group.name.ldap.attribute": "cn", "group.object.classes": "group",
             "preserve.group.inheritance": "false", "ignore.missing.groups": "false", "membership.ldap.attribute": "member",
             "membership.attribute.type": "DN", "membership.user.ldap.attribute": "cn", "mode": "READ_ONLY",
             "user.roles.retrieve.strategy": "LOAD_GROUPS_BY_MEMBER_ATTRIBUTE", "memberof.ldap.attribute": "memberOf",
             "drop.non.existing.groups.during.sync": "false", "groups.path": "/"})}]
    return r


def export_of(r, version="26.0.6"):
    """A realm of the fake as Keycloak's console exports it: other ids, the secrets masked, the flows as lists."""
    doc = dict(copy.deepcopy(r["rep"]), id="export-" + r["rep"]["id"], keycloakVersion=version)
    doc["clients"] = [dict(copy.deepcopy(c), id="export-" + c["id"], secret="**********",
                           protocolMappers=[dict(copy.deepcopy(m), id="export-" + m["id"]) for m in r["mappers"].get(c["id"], [])])
                      for c in r["clients"]]
    doc["groups"] = [{"id": "export-" + g["id"], "name": g["name"], "path": "/" + g["name"]} for g in r["groups"]]
    feds = []
    for c in r["components"]:
        if c["providerType"] != STORAGE:
            continue
        config = copy.deepcopy(c["config"])
        if "bindCredential" in config:
            config["bindCredential"] = ["**********"]
        subs = [{"id": "export-" + m["id"], "name": m["name"], "providerId": m["providerId"], "config": copy.deepcopy(m["config"])}
                for m in r["components"] if m.get("parentId") == c["id"]]
        feds.append({"id": "export-" + c["id"], "name": c["name"], "providerId": c["providerId"], "config": config,
                     "subComponents": {LDAP_MAPPER: subs}})
    doc["components"] = {STORAGE: feds}
    flows, configs = [], []
    for alias, steps in r["flows"].items():
        stack = [{"alias": alias, "topLevel": True, "authenticationExecutions": []}]
        flows.append(stack[0])
        for n, e in enumerate(steps):
            del stack[e["level"] + 1:]
            row = {"requirement": e["requirement"], "priority": n * 10, "authenticatorFlow": bool(e.get("authenticationFlow"))}
            if e.get("authenticationFlow"):
                row["flowAlias"] = e["displayName"]
                sub = {"alias": e["displayName"], "topLevel": False, "authenticationExecutions": []}
                flows.append(sub)
                stack[-1]["authenticationExecutions"].append(row)
                stack.append(sub)
                continue
            row["authenticator"] = e["providerId"]
            if e.get("authenticationConfig"):
                cfg = r["configs"][e["authenticationConfig"]]
                row["authenticatorConfig"] = cfg["alias"]
                configs.append({"id": "export-" + cfg["id"], "alias": cfg["alias"], "config": dict(cfg["config"])})
            stack[-1]["authenticationExecutions"].append(row)
    doc["authenticationFlows"], doc["authenticatorConfig"] = flows, configs
    return doc


class Keycloak(http.server.BaseHTTPRequestHandler):
    """Enough of Keycloak's admin API for the script: realms, flows, clients, mappers, groups, users, components."""
    protocol_version = "HTTP/1.1"
    realms = {}
    directory = []                             # (user name, the DNs of the groups the person is in)
    log = []
    counter = [0]

    def log_message(self, *a):
        pass

    @classmethod
    def reset(cls):
        cls.realms = {"master": new_realm("master"), "stigman": new_realm("stigman")}
        st = cls.realms["stigman"]
        st["clients"].append({"id": "c-stig", "clientId": "stig-manager", "redirectUris": ["https://stigman.yoursite.mil/*"]})
        st["groups"].append({"id": "g-stig", "name": "stig-admins"})
        st["users"].append({"id": "u-stig", "username": "roe.jane.a.1234567891", "email": "jane.roe@yoursite.mil"})
        cls.log = []
        cls.counter = [0]
        cls.directory = [("doe.john", [ACCESS]), ("poe.pat", [ACCESS, OPERATORS]), ("moe.max", [])]

    @classmethod
    def new_id(cls, word):
        cls.counter[0] += 1
        return "%s-%d" % (word, cls.counter[0])

    def answer(self, code, body=None):
        raw = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)
        self.close_connection = True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        self.handle_it("GET")

    def do_POST(self):
        self.handle_it("POST")

    def do_PUT(self):
        self.handle_it("PUT")

    def do_DELETE(self):
        self.handle_it("DELETE")

    def handle_it(self, method):
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        raw = self.body()
        Keycloak.log.append((method, path))
        if path == "/realms/master/protocol/openid-connect/token" and method == "POST":
            form = urllib.parse.parse_qs(raw.decode())
            if form.get("password") == [PASSWORD] and form.get("username") == ["admin"]:
                return self.answer(200, {"access_token": TOKEN})
            return self.answer(401, {"error": "invalid_grant", "error_description": "Invalid user credentials"})
        if not path.startswith("/admin/"):
            return self.answer(404, {"error": "not found"})
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self.answer(401, {"error": "HTTP 401 Unauthorized"})
        data = json.loads(raw.decode()) if raw.strip() else {}
        parts = [urllib.parse.unquote(x) for x in path.split("/")[2:]]            # realms, <name>, ...
        if parts == ["realms"] and method == "POST":
            name = data.get("realm")
            if name in Keycloak.realms:
                return self.answer(409, {"errorMessage": "Conflict detected. See logs for details"})
            r = new_realm(name, data.get("attributes"))
            r["rep"].update((k, v) for k, v in data.items() if k not in ("realm", "attributes"))
            Keycloak.realms[name] = r
            # Keycloak itself gives the master realm a client to manage the new realm with
            Keycloak.realms["master"]["clients"].append({"id": "c-" + name + "-realm", "clientId": name + "-realm"})
            return self.answer(201)
        if len(parts) < 2 or parts[0] != "realms" or parts[1] not in Keycloak.realms:
            return self.answer(404, {"error": "Realm not found."})
        r, rest = Keycloak.realms[parts[1]], parts[2:]
        try:
            return self.in_realm(method, r, rest, query, data)
        except KeyError:
            return self.answer(404, {"error": "Could not find resource"})

    def in_realm(self, method, r, rest, query, data):
        new_id = Keycloak.new_id
        if not rest:
            if method == "GET":
                return self.answer(200, r["rep"])
            if method == "PUT":
                r["rep"].update((k, v) for k, v in data.items() if k != "realm")
                if "attributes" in data and "browserSecurityHeaders" not in data and "browserSecurityHeaders" in r["rep"]:
                    r["rep"]["browserSecurityHeaders"] = {}        # Keycloak 26: attributes that are not sent back are dropped
                return self.answer(204)
        if rest[:2] == ["authentication", "flows"]:
            if len(rest) == 2 and method == "GET":
                return self.answer(200, [{"alias": a} for a in sorted(r["flows"])])
            if len(rest) == 4 and rest[3] == "copy" and method == "POST":
                new = data["newName"]
                if new in r["flows"]:
                    return self.answer(409, {"errorMessage": "New flow alias name already exists"})
                steps = copy.deepcopy(r["flows"][rest[2]])
                for e in steps:
                    e["id"] = new_id("ex")
                    if e.get("authenticationFlow"):
                        e["displayName"] = "%s %s" % (new, e["displayName"])
                r["flows"][new] = steps
                return self.answer(201)
            steps = r["flows"][rest[2]]
            if rest[3:] == ["executions"] and method == "GET":
                return self.answer(200, steps)
            if rest[3:] == ["executions"] and method == "PUT":
                for e in steps:
                    if e["id"] == data["id"]:
                        e["requirement"] = data["requirement"]
                        return self.answer(202)
                raise KeyError(data["id"])
            if rest[3:] == ["executions", "execution"] and method == "POST":
                # a new step goes to the end, DISABLED, as in Keycloak
                steps.append({"id": new_id("ex"), "level": 0, "providerId": data["provider"], "requirement": "DISABLED",
                              "displayName": "X509/Validate Username Form"})
                return self.answer(201)
        if rest[:2] == ["authentication", "executions"] and method == "POST":
            for steps in r["flows"].values():
                for i, e in enumerate(steps):
                    if e["id"] != rest[2]:
                        continue
                    if rest[3] == "raise-priority":
                        above = [j for j in range(i) if steps[j]["level"] == e["level"]]
                        if above:
                            j = above[-1]
                            block = [steps.pop(i)]
                            steps[j:j] = block
                        return self.answer(204)
                    if rest[3] == "config":
                        cid = new_id("cfg")
                        r["configs"][cid] = {"id": cid, "alias": data["alias"], "config": dict(data["config"])}
                        e["authenticationConfig"] = cid
                        return self.answer(201)
            raise KeyError(rest[2])
        if rest[:2] == ["authentication", "config"]:
            if method == "GET":
                return self.answer(200, r["configs"][rest[2]])
            if method == "PUT":
                r["configs"][rest[2]].update(alias=data["alias"], config=dict(data["config"]))
                return self.answer(204)
        if rest == ["users", "profile"]:
            if method == "GET":
                return self.answer(200, r["profile"])
            if method == "PUT":
                r["profile"] = data
                return self.answer(200, data)
        if rest[:1] == ["clients"]:
            if len(rest) == 1 and method == "GET":
                want = query.get("clientId", [None])[0]
                return self.answer(200, [dict(c, protocolMappers=r["mappers"].get(c["id"], [])) for c in r["clients"]
                                         if want in (None, c["clientId"])])
            if len(rest) == 1 and method == "POST":
                if any(c["clientId"] == data["clientId"] for c in r["clients"]):
                    return self.answer(409, {"errorMessage": "Client %s already exists" % data["clientId"]})
                c = dict(data, id=new_id("cl"))
                r["clients"].append(c)
                r["mappers"][c["id"]] = []
                r["client_default_scopes"][c["id"]] = ["profile", "email"]
                return self.answer(201)
            c = [x for x in r["clients"] if x["id"] == rest[1]]
            if not c:
                raise KeyError(rest[1])
            c = c[0]
            if len(rest) == 2 and method == "PUT":
                c.update(data)
                return self.answer(204)
            if rest[2:] == ["client-secret"] and method == "GET":
                return self.answer(200, {"type": "secret", "value": CLIENT_SECRET})
            if rest[2:] == ["default-client-scopes"] and method == "GET":
                return self.answer(200, [{"name": n} for n in r["client_default_scopes"].get(c["id"], [])])
            if rest[2:4] == ["protocol-mappers", "models"]:
                ms = r["mappers"].setdefault(c["id"], [])
                if len(rest) == 4 and method == "GET":
                    return self.answer(200, ms)
                if len(rest) == 4 and method == "POST":
                    if any(m["name"] == data["name"] for m in ms):
                        return self.answer(409, {"errorMessage": "Protocol mapper exists with same name"})
                    ms.append(dict(data, id=new_id("pm")))
                    return self.answer(201)
                if len(rest) == 5 and method == "PUT":
                    [m for m in ms if m["id"] == rest[4]][0].update(data)
                    return self.answer(204)
            if rest[2:3] == ["default-client-scopes"] and len(rest) == 4 and method == "PUT":
                name = [sc["name"] for sc in r["scopes"] if sc["id"] == rest[3]][0]
                r["client_default_scopes"].setdefault(c["id"], []).append(name)
                return self.answer(204)
        if rest == ["client-scopes"] and method == "GET":
            return self.answer(200, r["scopes"])
        if rest[:1] == ["client-scopes"] and rest[2:4] == ["protocol-mappers", "models"] and len(rest) == 5 and method == "PUT":
            sc = [x for x in r["scopes"] if x["id"] == rest[1]][0]
            [m for m in sc["protocolMappers"] if m.get("id") == rest[4]][0].update(data)
            return self.answer(204)
        if rest[:1] == ["groups"]:
            if method == "GET":
                return self.answer(200, r["groups"])
            if method == "POST":
                if any(g["name"] == data["name"] for g in r["groups"]):
                    return self.answer(409, {"errorMessage": "Top level group named '%s' already exists." % data["name"]})
                r["groups"].append({"id": new_id("gr"), "name": data["name"]})
                return self.answer(201)
        if rest[:1] == ["users"]:
            if rest == ["users", "count"] and method == "GET":
                return self.answer(200, len(r["users"]))
            if len(rest) == 1 and method == "GET":
                if "username" not in query:
                    first, most = int(query.get("first", ["0"])[0]), int(query.get("max", ["100"])[0])
                    return self.answer(200, r["users"][first:first + most])
                want = (query.get("username", [""])[0]).lower()
                return self.answer(200, [u for u in r["users"] if u["username"] == want])
            if len(rest) == 1 and method == "POST":
                name = data["username"].lower()                             # Keycloak keeps a user name in small letters
                if any(u["username"] == name for u in r["users"]):
                    return self.answer(409, {"errorMessage": "User exists with same username"})
                if data.get("email") and any(u.get("email") == data["email"] for u in r["users"]):
                    return self.answer(409, {"errorMessage": "User exists with same email"})
                r["users"].append(dict(data, username=name, id=new_id("us")))
                return self.answer(201)
            u = [x for x in r["users"] if x["id"] == rest[1]]
            if not u:
                raise KeyError(rest[1])
            if len(rest) == 2 and method == "PUT":
                u[0].update(data)
                return self.answer(204)
            if rest[2:] == ["groups"] and method == "GET":
                ids = r["user_groups"].get(rest[1], [])
                return self.answer(200, [g for g in r["groups"] if g["id"] in ids])
            if len(rest) == 4 and rest[2] == "groups" and method == "PUT":
                if not any(g["id"] == rest[3] for g in r["groups"]):
                    raise KeyError(rest[3])
                r["user_groups"].setdefault(rest[1], []).append(rest[3])
                return self.answer(204)
        if rest[:1] == ["components"]:
            if len(rest) == 1 and method == "GET":
                kind, parent = query.get("type", [None])[0], query.get("parent", [None])[0]
                return self.answer(200, [c for c in r["components"] if kind in (None, c.get("providerType"))
                                         and parent in (None, c.get("parentId"))])
            if len(rest) == 1 and method == "POST":
                r["components"].append(dict(data, id=new_id("co")))
                return self.answer(201)
            if len(rest) == 2 and method == "PUT":
                [c for c in r["components"] if c["id"] == rest[1]][0].update(data)
                return self.answer(204)
        if rest == ["testLDAPConnection"] and method == "POST":
            if data.get("bindCredential") != "**********" or not data.get("componentId"):
                return self.answer(400, {"errorMessage": "LDAP test error"})    # the script never sends the password again
            return self.answer(204)
        if rest[:1] == ["user-storage"] and method == "POST":
            fed = [c for c in r["components"] if c["id"] == rest[1]][0]
            if rest[2:] == ["sync"] and query.get("action") == ["triggerFullSync"]:
                # the directory's people, as far as the federation's filter lets them in
                group = (fed["config"].get("customUserSearchFilter") or [""])[0]
                added = 0
                for name, groups in Keycloak.directory:
                    if group and not any(g in group for g in groups):
                        continue
                    if not any(u["username"] == name for u in r["users"]):
                        r["users"].append({"id": new_id("us"), "username": name, "federationLink": fed["id"]})
                        added += 1
                return self.answer(200, {"added": added, "updated": 0, "removed": 0, "failed": 0, "status": "done"})
            if rest[2] == "mappers" and rest[4:] == ["sync"] and query.get("direction") == ["fedToKeycloak"]:
                m = [c for c in r["components"] if c["id"] == rest[3] and c.get("parentId") == fed["id"]][0]
                added = 0
                for name in re.findall(r"CN=([^,)]+)", (m["config"].get("groups.ldap.filter") or [""])[0]):
                    if not any(g["name"] == name for g in r["groups"]):
                        r["groups"].append({"id": new_id("gr"), "name": name})
                        added += 1
                return self.answer(200, {"added": added, "updated": 0, "removed": 0, "failed": 0, "status": "done"})
        return self.answer(405, {"error": "the fake does not know %s %s" % (method, "/".join(rest))})


# A stand-in for podman: "exec ... <container> kcadm.sh get|create|update ..." becomes a request to the fake Keycloak.
# It writes down every argument list it was called with.
RUNTIME = r'''#!/usr/bin/env python3
import json, os, sys, urllib.request, urllib.error
argv = sys.argv[1:]
with open(os.environ["FAKE_RUNTIME_LOG"], "a") as f:
    f.write(json.dumps(argv) + "\n")
if argv[:1] == ["ps"]:
    print("stigman-nginx\nstigman-keycloak")
    sys.exit(0)
assert argv[0] == "exec", argv
rest = argv[1:]
while rest and rest[0].startswith("-"):
    rest = rest[2:] if rest[0] == "--env" else rest[1:]
container, tool, args = rest[0], rest[1], rest[2:]
assert container == os.environ["FAKE_RUNTIME_CONTAINER"], container
if tool == "rm":
    sys.exit(0)
assert tool.endswith("kcadm.sh"), tool
if args[:2] == ["config", "credentials"]:
    if os.environ.get("KC_CLI_PASSWORD") != os.environ["FAKE_RUNTIME_PASSWORD"]:
        sys.stderr.write("Invalid user credentials [invalid_grant]\n")
        sys.exit(1)
    sys.exit(0)
verb, rel = args[0], args[1]
method = {"get": "GET", "create": "POST", "update": "PUT"}[verb]
data = sys.stdin.read().encode() if "-f" in args else None
req = urllib.request.Request(os.environ["FAKE_RUNTIME_URL"] + "/admin/" + rel, method=method, data=data)
req.add_header("Authorization", "Bearer " + os.environ["FAKE_RUNTIME_TOKEN"])
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        sys.stdout.write(r.read().decode())
except urllib.error.HTTPError as e:
    text = e.read().decode()
    e.close()
    sys.stderr.write("Resource not found for url\n" if e.code == 404 else "HTTP %s %s\n" % (e.code, text))
    sys.exit(1)
'''


class Base(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="aaprealm")
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Keycloak)
        cls.httpd.handle_error = lambda *a: None
        cls.url = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.pwfile = os.path.join(cls.tmp, "admin.pw")
        with open(cls.pwfile, "w") as f:
            f.write(PASSWORD + "\n")
        os.chmod(cls.pwfile, 0o600)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Keycloak.reset()

    def run_it(self, *args, **env):
        e = {"PATH": os.environ["PATH"], "KEYCLOAK_URL": "https://keycloak.yoursite.mil", "KEYCLOAK_ADMIN_URL": self.url,
             "AAP_URL": "https://aap.yoursite.mil", "KEYCLOAK_ADMIN_PASSWORD_FILE": self.pwfile,
             "KEYCLOAK_PROTECTED_REALMS": "master,stigman", "KEYCLOAK_TIMEOUT": "10"}
        e.update(env)
        e = {k: v for k, v in e.items() if v is not None}
        via = () if "--via" in args else ("--via", "rest")
        r = subprocess.run([sys.executable, SCRIPT] + list(via) + list(args), capture_output=True, text=True, env=e,
                           stdin=subprocess.DEVNULL, timeout=120)
        return r.returncode, r.stdout, r.stderr

    def state(self):
        return json.dumps(Keycloak.realms, sort_keys=True)

    def writes(self):
        """Every request that changes something. Keycloak's own try of the LDAP bind changes nothing and is left out."""
        return [(m, p) for m, p in Keycloak.log if m != "GET" and not p.endswith("/openid-connect/token")
                and not p.endswith("/testLDAPConnection")]

    def ldap(self, **more):
        bind = os.path.join(self.tmp, "bind.pw")
        with open(bind, "w") as f:
            f.write(LDAP_PASSWORD + "\n")
        os.chmod(bind, 0o600)
        env = dict(LDAP_URL="ldaps://dc01.corp.example.mil:636", LDAP_USERS_DN="OU=People,DC=corp,DC=example,DC=mil",
                   LDAP_BIND_DN="CN=svc-keycloak,OU=Service,DC=corp,DC=example,DC=mil", LDAP_BIND_PASSWORD_FILE=bind,
                   LDAP_GROUP_DN=ACCESS)
        env.update(more)
        return env

    def lines(self, out, word):
        return [ln for ln in out.splitlines() if ln.startswith(word + " ")]


class TheRealm(Base):

    def test_dry_run_changes_nothing(self):
        before = self.state()
        rc, out, err = self.run_it()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.writes(), [])
        self.assertIn("DRY RUN: nothing is changed", out)
        self.assertEqual(len(self.lines(out, "CREATE")) + len(self.lines(out, "CHANGE")), 12, out)
        for said in ("CREATE  realm aap", "CREATE  flow browser-x509: a copy of the browser flow",
                     "CHANGE  flow browser-x509: the password forms DISABLED", "CHANGE  realm aap: its browser flow is browser-x509",
                     "CREATE  client aap (confidential, standard flow only, redirect URI "
                     "https://aap.yoursite.mil/api/gateway/social/complete/*)",
                     "CREATE  client aap: the mapper groups", "CREATE  client aap: the mapper audience",
                     "CREATE  group aap-admins", "CREATE  group aap-operators", "CREATE  group aap-users",
                     "12 changes would be made. Nothing was changed (dry run).", "to another realm: 0."):
            self.assertIn(said, out, said)
        self.assertNotIn("aap", Keycloak.realms)

    def test_dry_run_is_the_default_and_can_be_said(self):
        a = self.run_it()
        b = self.run_it("--dry-run")
        self.assertEqual(a, b)
        self.assertEqual(self.writes(), [])

    def test_apply_makes_the_realm(self):
        rc, out, err = self.run_it("--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("12 changes made in the realm aap.", out)
        r = Keycloak.realms["aap"]
        self.assertEqual(r["rep"]["attributes"], {MARK: "1"})
        self.assertTrue(r["rep"]["enabled"])
        self.assertFalse(r["rep"]["registrationAllowed"])
        self.assertEqual(r["rep"]["browserFlow"], "browser-x509")
        top = [e for e in r["flows"]["browser-x509"] if e["level"] == 0]
        kinds = [e.get("providerId") or "flow" for e in top]
        self.assertEqual(kinds, ["auth-cookie", "identity-provider-redirector", X509, "flow"])
        card, forms = top[2], top[3]
        self.assertEqual(card["requirement"], "ALTERNATIVE")
        self.assertEqual(forms["requirement"], "DISABLED", "the card is the only way in")
        cfg = r["configs"][card["authenticationConfig"]]["config"]
        self.assertEqual(cfg["x509-cert-auth.mapping-source-selection"], "Subject's Common Name")
        self.assertEqual(cfg["x509-cert-auth.mapper-selection"], "Username or Email")
        self.assertEqual(cfg["x509-cert-auth.ocsp-checking-enabled"], "false")
        self.assertEqual([e["requirement"] for e in r["flows"]["browser"]],
                         ["ALTERNATIVE", "ALTERNATIVE", "ALTERNATIVE", "REQUIRED"], "Keycloak's own flow is left as it was")
        (c,) = r["clients"]
        self.assertEqual(c["clientId"], "aap")
        self.assertFalse(c["publicClient"])
        self.assertTrue(c["standardFlowEnabled"])
        self.assertFalse(c["directAccessGrantsEnabled"])
        self.assertFalse(c["implicitFlowEnabled"])
        self.assertEqual(c["redirectUris"], ["https://aap.yoursite.mil/api/gateway/social/complete/*"])
        ms = dict((m["name"], m) for m in r["mappers"][c["id"]])
        self.assertEqual(sorted(ms), ["audience", "groups"])
        self.assertEqual(ms["groups"]["protocolMapper"], "oidc-group-membership-mapper")
        self.assertEqual(ms["groups"]["config"]["claim.name"], "groups")
        self.assertEqual(ms["groups"]["config"]["full.path"], "false")
        self.assertEqual(ms["audience"]["config"]["included.client.audience"], "aap")
        self.assertEqual(sorted(g["name"] for g in r["groups"]), ["aap-admins", "aap-operators", "aap-users"])
        self.assertEqual(r["users"], [], "nobody is made without a list")
        self.assertIn("People: none are made.", out)

    def test_second_apply_changes_nothing(self):
        self.assertEqual(self.run_it("--apply")[0], 0)
        before = self.state()
        Keycloak.log = []
        rc, out, err = self.run_it("--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.lines(out, "CREATE") + self.lines(out, "CHANGE"), [], out)
        self.assertIn("Nothing to change: the realm aap is as the settings say.", out)
        self.assertGreaterEqual(len(self.lines(out, "ok")), 10, out)

    def test_another_realm_is_untouched(self):
        """STIG Manager's realm is the same before and after, and no request named it."""
        stigman = json.dumps(Keycloak.realms["stigman"], sort_keys=True)
        master = copy.deepcopy(Keycloak.realms["master"])
        for args in ((), ("--apply",), ("--apply",)):
            rc, out, err = self.run_it(*args)
            self.assertEqual(rc, 0, out + err)
            self.assertIn("to another realm: 0.", out)
        self.assertEqual(json.dumps(Keycloak.realms["stigman"], sort_keys=True), stigman)
        seen = set()
        for method, path in Keycloak.log:
            if path == "/realms/master/protocol/openid-connect/token":
                continue                                                      # the admin login
            if (method, path) == ("POST", "/admin/realms"):
                seen.add("create")
                continue
            self.assertTrue(path == "/admin/realms/aap" or path.startswith("/admin/realms/aap/"), (method, path))
            self.assertNotEqual(method, "DELETE")
        self.assertEqual(seen, {"create"})
        self.assertEqual(Keycloak.log.count(("POST", "/admin/realms")), 1)
        # what changed in master is Keycloak's own doing: the client it adds for every new realm
        now = Keycloak.realms["master"]
        self.assertEqual([c["clientId"] for c in now["clients"]], ["aap-realm"])
        now = dict(now, clients=[])
        self.assertEqual(now, master)

    def test_a_protected_realm_is_refused_before_any_request(self):
        for realm in ("stigman", "master", "Stigman"):
            rc, out, err = self.run_it("--apply", KEYCLOAK_REALM=realm)
            self.assertEqual(rc, 2, (realm, out, err))
            self.assertIn("is a protected realm", err)
            self.assertEqual(Keycloak.log, [], realm)

    def test_a_realm_it_did_not_make_is_left_alone(self):
        Keycloak.realms["tools"] = new_realm("tools")
        before = self.state()
        rc, out, err = self.run_it(KEYCLOAK_REALM="tools")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("note    the realm tools is there and this script did not make it. A dry run only reads it; "
                      "--apply needs --adopt.", out)
        self.assertEqual(self.writes(), [])
        rc, out, err = self.run_it("--apply", KEYCLOAK_REALM="tools")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("this script did not make it", err)
        self.assertIn("--adopt", err)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state(), before)
        rc, out, err = self.run_it("--apply", "--adopt", KEYCLOAK_REALM="tools")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("CREATE  realm tools: the mark that this is AAP's realm (--adopt)", out)
        self.assertEqual(Keycloak.realms["tools"]["rep"]["attributes"], {MARK: "1"})
        rc, out, err = self.run_it("--apply", KEYCLOAK_REALM="tools")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Nothing to change", out)

    def test_a_field_that_differs_is_said_and_left(self):
        """A thing that is there is compared. A field that differs is printed and left, until --update-existing."""
        self.assertEqual(self.run_it("--apply")[0], 0)
        exact = "https://aap.yoursite.mil/api/gateway/social/complete/ansible_base-authentication-authenticator_plugins-oidc__keycloak/"
        line = "differs client aap: redirectUris is https://aap.yoursite.mil/api/gateway/social/complete/*, settings say " + exact
        Keycloak.log[:] = []
        for args in ((), ("--apply",)):
            rc, out, err = self.run_it(*args, AAP_REDIRECT_URI=exact)
            self.assertEqual(rc, 0, out + err)
            self.assertEqual(self.lines(out, "differs"), [line])
            self.assertEqual(self.lines(out, "CHANGE") + self.lines(out, "CREATE"), [])
            self.assertIn("1 field of things that are there differs from the settings", out)
            self.assertIn("add --update-existing", out)
            self.assertIn("Nothing to change: nothing is missing in the realm aap.", out)
            self.assertEqual(self.writes(), [], "said, and left as it is")
        rc, out, err = self.run_it("--update-existing", AAP_REDIRECT_URI=exact)
        self.assertEqual(self.lines(out, "CHANGE"), ["CHANGE  client aap: redirectUris https://aap.yoursite.mil/api/gateway/"
                                                     "social/complete/* -> " + exact])
        self.assertIn("1 change would be made.", out)
        self.assertEqual(self.writes(), [], "--update-existing without --apply is a dry run")
        rc, out, err = self.run_it("--apply", "--update-existing", AAP_REDIRECT_URI=exact)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(Keycloak.realms["aap"]["clients"][0]["redirectUris"], [exact])
        self.assertEqual([m for m, p in self.writes()], ["PUT"], "one request, for the one thing that differed")

    def test_changed_settings_are_single_changes(self):
        self.assertEqual(self.run_it("--apply")[0], 0)
        rc, out, err = self.run_it("--apply", AAP_GROUPS="aap-admins,aap-operators,aap-users,aap-auditors")
        self.assertEqual(self.lines(out, "CREATE"), ["CREATE  group aap-auditors"], "a missing thing needs no flag")
        self.assertEqual(self.lines(out, "CHANGE"), [])
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", "--update-existing", X509_PASSWORD_FORM="on", X509_OCSP="on")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.lines(out, "CHANGE")), 2, out)
        self.assertIn("ocsp-checking-enabled false -> true", out)
        self.assertIn("requirement DISABLED -> ALTERNATIVE", out)
        self.assertEqual(len(self.writes()), 2, self.writes())
        self.assertEqual(len(Keycloak.realms["aap"]["groups"]), 4, "a group no longer named is not removed")
        self.assertNotIn("DELETE", [m for m, p in Keycloak.log])

    def test_what_names_the_person(self):
        for how, source, extra in (("edipi", "Match SubjectDN using regular expression", {}),
                                   ("upn", "Subject's Alternative Name otherName (UPN)", {}),
                                   ("email", "Subject's Alternative Name E-mail", {}),
                                   ("regex", "Match SubjectDN using regular expression", {"X509_REGEX": "CN=(.*?)(?:,|$)"})):
            Keycloak.reset()
            rc, out, err = self.run_it("--apply", X509_IDENTITY=how, **extra)
            self.assertEqual(rc, 0, how + out + err)
            (cfg,) = Keycloak.realms["aap"]["configs"].values()
            self.assertEqual(cfg["config"]["x509-cert-auth.mapping-source-selection"], source, how)
        mod = load()
        s = dict(mod.DEFAULTS, X509_IDENTITY="edipi")
        rx = mod.x509_config(s)["x509-cert-auth.regular-expression"]
        self.assertEqual(re.search(rx, "CN=DOE.JOHN.Q.1234567890,OU=PKI,OU=DoD,O=U.S. Government,C=US").group(1), "1234567890")
        self.assertIsNone(re.search(rx, "CN=web01.yoursite.mil,OU=PKI"))
        rc, out, err = self.run_it(X509_IDENTITY="dn")
        self.assertEqual(rc, 2)
        rc, out, err = self.run_it(X509_IDENTITY="regex", X509_REGEX="CN=.*")
        self.assertEqual(rc, 2, "an expression with no group names nobody")

    def test_a_user_attribute(self):
        people = os.path.join(self.tmp, "attr.csv")
        with open(people, "w") as f:
            f.write("jdoe,john.doe@yoursite.mil,John,Doe,aap-admins,1234567890\n")
        rc, out, err = self.run_it("--apply", X509_IDENTITY="edipi", X509_USER_ATTRIBUTE="edipi", AAP_USERS_FILE=people)
        self.assertEqual(rc, 0, out + err)
        r = Keycloak.realms["aap"]
        self.assertIn("edipi", [a["name"] for a in r["profile"]["attributes"]])
        (cfg,) = r["configs"].values()
        self.assertEqual(cfg["config"]["x509-cert-auth.mapper-selection"], "Custom Attribute Mapper")
        self.assertEqual(cfg["config"]["x509-cert-auth.mapper-selection.user-attribute-name"], "edipi")
        self.assertEqual(r["users"][0]["attributes"], {"edipi": ["1234567890"]})
        rc, out, err = self.run_it("--apply", X509_IDENTITY="edipi", X509_USER_ATTRIBUTE="edipi", AAP_USERS_FILE=people)
        self.assertIn("Nothing to change", out)

    def test_people_from_a_list(self):
        people = os.path.join(self.tmp, "people.csv")
        with open(people, "w") as f:
            f.write("username,email,first name,last name,groups\n"
                    "DOE.JOHN.Q.1234567890,john.doe@yoursite.mil,John,Doe,aap-admins;aap-users\n"
                    "# a note\n"
                    "ROE.JANE.A.1234567891,jane.roe@yoursite.mil,Jane,Roe,aap-operators\n")
        rc, out, err = self.run_it(AAP_USERS_FILE=people)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("CREATE  person DOE.JOHN.Q.1234567890 in aap-admins, aap-users", out)
        self.assertIn("14 changes would be made.", out)
        self.assertEqual(self.writes(), [])
        rc, out, err = self.run_it("--apply", AAP_USERS_FILE=people)
        self.assertEqual(rc, 0, out + err)
        r = Keycloak.realms["aap"]
        self.assertEqual(sorted(u["username"] for u in r["users"]), ["doe.john.q.1234567890", "roe.jane.a.1234567891"])
        doe = [u for u in r["users"] if u["username"].startswith("doe")][0]
        names = sorted(g["name"] for g in r["groups"] if g["id"] in r["user_groups"][doe["id"]])
        self.assertEqual(names, ["aap-admins", "aap-users"])
        self.assertNotIn("People: none are made.", out)
        Keycloak.log = []
        rc, out, err = self.run_it("--apply", AAP_USERS_FILE=people)
        self.assertIn("Nothing to change", out)
        self.assertIn("ok      2 of 2 people of people.csv are there, in their groups", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(Keycloak.realms["stigman"]["users"]), 1)
        with open(people, "a") as f:
            f.write("POE.SAM.B.1234567892,sam.poe@yoursite.mil,Sam,Poe,aap-root\n")
        rc, out, err = self.run_it("--apply", AAP_USERS_FILE=people)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("a group that AAP_GROUPS does not name: aap-root", err)

    def test_a_read_only_federation(self):
        env = self.ldap()
        rc, out, err = self.run_it(**env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("CREATE  user federation directory: ldaps://dc01.corp.example.mil:636, READ_ONLY, "
                      "the user name from sAMAccountName\n"
                      "        lets in only the members of %s (nested groups count)\n"
                      "        the changed people are read every 900 s, all of them every 86400 s\n" % ACCESS, out)
        self.assertIn("the card names the person by the UPN in the subject alternative name (1234567890123456@mil), "
                      "looked up in the user attribute aap-upn", out, "with a directory the card's UPN is the default")
        self.assertIn("the LDAP attribute userPrincipalName is read into the user attribute aap-upn", out)
        self.assertNotIn("CREATE  group", out, "the aap-* groups are for a realm without a directory")
        self.assertEqual(self.writes(), [])
        rc, out, err = self.run_it("--apply", **env)
        self.assertEqual(rc, 0, out + err)
        c, upn = Keycloak.realms["aap"]["components"]
        cfg = {k: v[0] for k, v in c["config"].items()}
        self.assertEqual((c["providerId"], c["providerType"], c["parentId"]), ("ldap", STORAGE, "id-aap"))
        for k, v in (("editMode", "READ_ONLY"), ("importEnabled", "true"), ("syncRegistrations", "false"), ("vendor", "ad"),
                     ("changedSyncPeriod", "900"), ("fullSyncPeriod", "86400"), ("startTls", "false"),
                     ("useTruststoreSpi", "always"), ("bindCredential", LDAP_PASSWORD),
                     ("customUserSearchFilter", "(memberOf:1.2.840.113556.1.4.1941:=%s)" % ACCESS)):
            self.assertEqual(cfg[k], v, k)
        self.assertEqual((upn["providerId"], upn["parentId"]), ("user-attribute-ldap-mapper", c["id"]))
        self.assertEqual(upn["config"]["ldap.attribute"], ["userPrincipalName"])
        self.assertEqual(upn["config"]["read.only"], ["true"])
        self.assertEqual(Keycloak.realms["aap"]["groups"], [])
        self.assertIn("note    Keycloak reached ldaps://dc01.corp.example.mil:636 and the bind account was accepted", out)
        self.assertNotIn(LDAP_PASSWORD, out + err)
        self.assertNotIn("People: none are made.", out)
        self.assertIn("Out of the group: refused at the next login.", out)
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", **env)
        self.assertIn("Nothing to change: the realm aap is as the settings say.", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(c["config"]["bindCredential"], [LDAP_PASSWORD], "the password is not sent a second time")
        self.assertEqual(Keycloak.realms["stigman"]["components"], [])

    def test_the_federation_s_settings(self):
        for env, said in ((dict(LDAP_URL="ldaps://dc01.corp.example.mil:636"), "LDAP_URL needs LDAP_USERS_DN and LDAP_BIND_DN"),
                          (self.ldap(LDAP_GROUP_DN="AAP-Access"), "LDAP_GROUP_DN"),
                          (self.ldap(LDAP_TEAM_GROUPS="AAP-Admins"), "LDAP_TEAM_GROUPS"),
                          (self.ldap(LDAP_SYNC_CHANGED_SECONDS="soon"), "LDAP_SYNC_CHANGED_SECONDS"),
                          (self.ldap(LDAP_URL="https://dc01.corp.example.mil"), "LDAP_URL")):
            rc, out, err = self.run_it(**env)
            self.assertEqual(rc, 2, out + err)
            self.assertIn(said, err)
            self.assertEqual(Keycloak.log, [], "refused before any request")
        rc, out, err = self.run_it("--sync")
        self.assertEqual(rc, 2)
        self.assertIn("LDAP_URL", err)

    def test_only_the_group_s_members_and_nested_or_not(self):
        rc, out, err = self.run_it("--apply", **self.ldap(LDAP_GROUP_NESTED="off", LDAP_STARTTLS="on",
                                                          LDAP_URL="ldap://dc01.corp.example.mil:389"))
        self.assertEqual(rc, 0, out + err)
        cfg = Keycloak.realms["aap"]["components"][0]["config"]
        self.assertEqual(cfg["customUserSearchFilter"], ["(memberOf=%s)" % ACCESS])
        self.assertEqual(cfg["startTls"], ["true"])
        self.assertIn("lets in only the members of %s (direct members only)" % ACCESS, out)
        # nested or not is one field of a federation that is there: said, and changed only when asked
        Keycloak.log[:] = []
        env = self.ldap(LDAP_STARTTLS="on", LDAP_URL="ldap://dc01.corp.example.mil:389")
        rc, out, err = self.run_it("--apply", **env)
        self.assertEqual(self.lines(out, "differs"), [
            "differs user federation directory: customUserSearchFilter is (memberOf=%s), settings say "
            "(memberOf:1.2.840.113556.1.4.1941:=%s)" % (ACCESS, ACCESS)])
        self.assertEqual(self.writes(), [])
        rc, out, err = self.run_it("--apply", "--update-existing", **env)
        cfg = Keycloak.realms["aap"]["components"][0]["config"]
        self.assertEqual(cfg["customUserSearchFilter"], ["(memberOf:1.2.840.113556.1.4.1941:=%s)" % ACCESS])
        self.assertEqual(cfg["bindCredential"], [LDAP_PASSWORD], "the password Keycloak holds is kept")
        self.assertEqual(len(self.writes()), 1)

    def test_no_group_named_lets_everybody_under_the_users_dn_in_and_says_so(self):
        rc, out, err = self.run_it(**self.ldap(LDAP_GROUP_DN=None))
        self.assertEqual(rc, 0, out + err)
        self.assertIn("lets in everybody under OU=People,DC=corp,DC=example,DC=mil", out)
        self.assertIn("WARNING LDAP_GROUP_DN is not set", out)
        rc, out, err = self.run_it("--apply", **self.ldap(LDAP_GROUP_DN=None))
        self.assertEqual(Keycloak.realms["aap"]["components"][0]["config"].get("customUserSearchFilter", [""]), [""])
        # a filter that is there is never taken away by settings that name no group
        fed = Keycloak.realms["aap"]["components"][0]
        fed["config"]["customUserSearchFilter"] = ["(memberOf=%s)" % ACCESS]
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", "--update-existing", **self.ldap(LDAP_GROUP_DN=None))
        self.assertEqual(rc, 0, out + err)
        self.assertIn("its filter is (memberOf=%s) (LDAP_GROUP_DN is not set: not compared)" % ACCESS, out)
        self.assertEqual(self.writes(), [])

    def test_team_groups_and_a_sync(self):
        env = self.ldap(LDAP_TEAM_GROUPS=OPERATORS + ";" + ADMINS)
        rc, out, err = self.run_it("--sync", **env)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.writes(), [], "--sync without --apply reads nothing in and changes nothing")
        rc, out, err = self.run_it("--apply", "--sync", **env)
        self.assertEqual(rc, 0, out + err)
        team = Keycloak.realms["aap"]["components"][2]
        self.assertEqual((team["name"], team["providerId"]), ("aap-teams", "group-ldap-mapper"))
        cfg = {k: v[0] for k, v in team["config"].items()}
        self.assertEqual(cfg["mode"], "READ_ONLY")
        self.assertEqual(cfg["groups.dn"], "OU=Groups,DC=corp,DC=example,DC=mil")
        self.assertEqual(cfg["groups.ldap.filter"], "(|(distinguishedName=%s)(distinguishedName=%s))" % (OPERATORS, ADMINS))
        self.assertEqual(cfg["user.roles.retrieve.strategy"], "LOAD_GROUPS_BY_MEMBER_ATTRIBUTE_RECURSIVELY")
        self.assertEqual(cfg["drop.non.existing.groups.during.sync"], "false", "a sync never removes a Keycloak group")
        self.assertIn("SYNC    a full sync of the directory: 2 added, 0 updated, 0 removed, 0 failed", out)
        self.assertIn("2 of the realm's people come from the directory now", out)
        self.assertEqual(sorted(u["username"] for u in Keycloak.realms["aap"]["users"]), ["doe.john", "poe.pat"],
                         "moe.max is not in the group and is not taken in")
        self.assertEqual(sorted(g["name"] for g in Keycloak.realms["aap"]["groups"]), ["AAP-Admins", "AAP-Operators"])
        self.assertIn("Groups Claim               groups", out)

    def test_the_secret_goes_to_a_file_only(self):
        dest = os.path.join(self.tmp, "client.secret")
        rc, out, err = self.run_it("--secret-file", dest)
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(os.path.exists(dest), "a dry run writes no file")
        self.assertIn("only with --apply", out)
        rc, out, err = self.run_it("--apply", "--secret-file", dest)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(read(dest), CLIENT_SECRET + "\n")
        self.assertEqual(stat.S_IMODE(os.stat(dest).st_mode), 0o600)
        self.assertNotIn(CLIENT_SECRET, out + err)


class TheHandMadeRealm(Base):
    """The realm is there already, made in Keycloak's console: adopt it, never clobber it."""

    def setUp(self):
        Keycloak.reset()
        Keycloak.realms["aap"] = hand_realm()
        self.env = self.ldap(LDAP_TEAM_GROUPS=OPERATORS + ";" + ADMINS)

    def kept(self):
        """What a person made by hand and the settings do not name: it must be there, unchanged, after every run."""
        r = Keycloak.realms["aap"]
        return json.dumps([r["rep"]["displayName"], r["rep"]["browserFlow"], r["rep"]["browserSecurityHeaders"], r["groups"],
                           r["users"], sorted(r["flows"]), [c["name"] for c in r["components"]],
                           [c for c in r["components"] if c["name"] in ("employee_ID", "MSAD account controls", "aap-upn")],
                           r["profile"], r["configs"]["h-cfg"]["alias"], [c["clientId"] for c in r["clients"]]], sort_keys=True)

    DIFFERS = [
        "differs flow aap browser: the card step: confirmation-page-disallowed is false, settings say true",
        "differs flow aap browser: the password forms (aap browser forms): requirement is ALTERNATIVE, settings say DISABLED",
        "differs client aap: directAccessGrantsEnabled is true, settings say false",
        "differs client aap: redirectUris is https://aap.yoursite.mil/api/gateway/social/complete/oidc/, settings say "
        "https://aap.yoursite.mil/api/gateway/social/complete/*",
        "differs user federation corp-ad: changedSyncPeriod is -1, settings say 900",
        "differs user federation corp-ad: customUserSearchFilter is (not set), settings say "
        "(memberOf:1.2.840.113556.1.4.1941:=%s)" % ACCESS,
        "differs user federation corp-ad: fullSyncPeriod is -1, settings say 86400",
        "differs user federation corp-ad: the group mapper aap-groups: user.roles.retrieve.strategy is "
        "LOAD_GROUPS_BY_MEMBER_ATTRIBUTE, settings say LOAD_GROUPS_BY_MEMBER_ATTRIBUTE_RECURSIVELY"]

    def test_the_dry_run_says_what_differs_and_changes_nothing(self):
        before = self.state()
        rc, out, err = self.run_it(**self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("note    the realm aap is there and this script did not make it. A dry run only reads it; "
                      "--apply needs --adopt.", out)
        self.assertEqual(self.lines(out, "differs"), self.DIFFERS)
        self.assertEqual([ln.split(" (")[0] for ln in self.lines(out, "CREATE")],
                         ["CREATE  client aap: the mapper groups", "CREATE  client aap: the mapper audience"],
                         "only what is missing would be made")
        self.assertEqual(self.lines(out, "CHANGE"), [])
        self.assertIn("8 fields of things that are there differ from the settings", out)
        self.assertIn("2 changes would be made.", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state(), before)

    def test_things_are_found_by_what_they_are_not_by_their_name(self):
        rc, out, err = self.run_it(**self.env)
        self.assertIn("found   user federation corp-ad: the realm's LDAP federation. It is used under its own name", out)
        self.assertIn("found   flow aap browser: the realm's browser flow, with the card step in it. It is used as it is", out)
        self.assertIn("is read into the user attribute aap-upn (what the card is looked up in), by the mapper aap-upn", out)
        self.assertIn("the group mapper aap-groups", out)
        for made in ("user federation directory", "flow browser-x509", "aap-teams"):
            self.assertNotIn(made, out.replace("X509_FLOW=browser-x509", ""), "no second one beside the hand-made one")

    def test_apply_needs_adopt_and_adopt_adds_only_the_mark_and_what_is_missing(self):
        before, kept = self.state(), self.kept()
        rc, out, err = self.run_it("--apply", **self.env)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("this script did not make it", err)
        self.assertIn("--adopt", err)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state(), before)
        rc, out, err = self.run_it("--apply", "--adopt", **self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("CREATE  realm aap: the mark that this is AAP's realm (--adopt). Nothing else of the realm changes by it", out)
        self.assertEqual(self.lines(out, "differs"), self.DIFFERS, "said again, and left")
        self.assertEqual(self.lines(out, "CHANGE"), [])
        self.assertIn("3 changes made in the realm aap.", out)
        r = Keycloak.realms["aap"]
        self.assertEqual(r["rep"]["attributes"], {MARK: "1"})
        self.assertEqual(self.kept(), kept)
        self.assertEqual([m["name"] for m in r["mappers"]["h-client"]], ["groups", "audience"])
        self.assertEqual(sorted(m for m, p in self.writes()), ["POST", "POST", "PUT"], self.writes())
        # every field that differed is as the person set it
        self.assertEqual(r["clients"][0]["redirectUris"], ["https://aap.yoursite.mil/api/gateway/social/complete/oidc/"])
        self.assertTrue(r["clients"][0]["directAccessGrantsEnabled"])
        self.assertEqual(r["configs"]["h-cfg"]["config"]["x509-cert-auth.confirmation-page-disallowed"], "false")
        self.assertEqual(r["flows"]["aap browser"][2]["requirement"], "ALTERNATIVE")
        self.assertNotIn("customUserSearchFilter", r["components"][0]["config"])
        self.assertEqual(len(r["components"]), 5)
        # from now on --apply works, and still changes nothing that is there
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", **self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Nothing to change: nothing is missing in the realm aap.", out)
        self.assertEqual(self.writes(), [])

    def test_update_existing_changes_only_the_fields_that_differ(self):
        self.assertEqual(self.run_it("--apply", "--adopt", **self.env)[0], 0)
        kept = self.kept()
        r = Keycloak.realms["aap"]
        fed_before = copy.deepcopy(r["components"][0]["config"])
        groups_before = copy.deepcopy(r["components"][4]["config"])
        card_before = dict(r["configs"]["h-cfg"]["config"])
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", "--update-existing", **self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.lines(out, "differs"), [])
        self.assertEqual(len(self.lines(out, "CHANGE")), 5, out)
        self.assertIn("5 changes made in the realm aap.", out)
        self.assertEqual([m for m, p in self.writes()], ["PUT"] * 5 + ["POST"], self.writes())
        self.assertTrue(self.writes()[-1][1].endswith("/user-storage/h-fed/mappers/h-groups/sync"),
                        "the one request that is no change of a field: the changed mapper reads its groups again")

        def changed(before, after):
            return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        self.assertEqual(changed(fed_before, r["components"][0]["config"]),
                         ["changedSyncPeriod", "customUserSearchFilter", "fullSyncPeriod"])
        self.assertEqual(r["components"][0]["config"]["trustEmail"], ["false"], "a field the settings do not compare")
        self.assertEqual(r["components"][0]["name"], "corp-ad", "never renamed")
        self.assertEqual(changed(groups_before, r["components"][4]["config"]), ["user.roles.retrieve.strategy"])
        self.assertNotIn("groups.ldap.filter", r["components"][4]["config"], "the hand-made mapper keeps all its groups")
        self.assertEqual(changed(card_before, r["configs"]["h-cfg"]["config"]), ["x509-cert-auth.confirmation-page-disallowed"])
        client = r["clients"][0]
        self.assertEqual((client["name"], client["redirectUris"], client["directAccessGrantsEnabled"]),
                         ("AAP", ["https://aap.yoursite.mil/api/gateway/social/complete/*"], False))
        self.assertEqual(self.kept(), kept)
        Keycloak.log[:] = []
        rc, out, err = self.run_it("--apply", "--update-existing", **self.env)
        self.assertIn("Nothing to change: the realm aap is as the settings say.", out)
        self.assertEqual(self.writes(), [])

    def test_nothing_is_ever_deleted_or_renamed(self):
        names = [c["name"] for c in Keycloak.realms["aap"]["components"]]
        for args in (("--apply", "--adopt"), ("--apply", "--update-existing"), ("--apply", "--update-existing", "--sync")):
            self.assertEqual(self.run_it(*args, **self.env)[0], 0)
        r = Keycloak.realms["aap"]
        self.assertNotIn("DELETE", [m for m, p in Keycloak.log])
        self.assertEqual([c["name"] for c in r["components"]], names)
        self.assertIn("keep-me", [g["name"] for g in r["groups"]])
        self.assertIn("local.admin", [u["username"] for u in r["users"]])
        self.assertEqual(sorted(r["flows"]), ["aap browser", "browser"])
        self.assertEqual(r["rep"]["browserFlow"], "aap browser")

    def test_the_mark_keeps_the_rest_of_the_realm(self):
        """Keycloak drops what a realm update leaves out: the mark goes in with the whole realm, as it was read."""
        self.assertEqual(self.run_it("--apply", "--adopt", **self.env)[0], 0)
        rep_ = Keycloak.realms["aap"]["rep"]
        self.assertEqual(rep_["browserSecurityHeaders"], {"contentSecurityPolicy": "frame-src 'self'", "xFrameOptions": "SAMEORIGIN"})
        self.assertEqual(rep_["displayName"], "AAP (by hand)")

    def test_a_client_with_no_groups_claim_is_offered_the_mapper(self):
        rc, out, err = self.run_it(**self.env)
        self.assertIn('CREATE  client aap: the mapper groups (the groups, short names, as the claim "groups" in the ID token, '
                      'the access token and userinfo)', out)
        # a Group Membership mapper under another name is the one: no second mapper beside it
        Keycloak.realms["aap"]["mappers"]["h-client"].append({
            "id": "h-pm", "name": "team membership", "protocol": "openid-connect", "protocolMapper": "oidc-group-membership-mapper",
            "config": {"claim.name": "groups", "full.path": "true", "id.token.claim": "true", "access.token.claim": "true",
                       "userinfo.token.claim": "true"}})
        rc, out, err = self.run_it(**self.env)
        self.assertNotIn("CREATE  client aap: the mapper groups", out)
        self.assertIn("team membership", out)
        self.assertIn("full.path is true, settings say false", out)


class TheCompare(Base):
    """--compare: this realm against a realm export of another Keycloak. It reads; it never imports."""

    def setUp(self):
        Keycloak.reset()
        self.env = self.ldap(LDAP_TEAM_GROUPS=OPERATORS + ";" + ADMINS)
        self.assertEqual(self.run_it("--apply", **self.env)[0], 0)
        self.path = os.path.join(self.tmp, "realm-export.json")
        Keycloak.log[:] = []

    def export(self, doc):
        with open(self.path, "w") as f:
            json.dump(doc, f)

    def test_its_own_export_is_the_same(self):
        self.export(export_of(Keycloak.realms["aap"]))
        before = self.state()
        rc, out, err = self.run_it("--compare", self.path, **self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("COMPARE - the realm aap here with realm-export.json (the realm aap, Keycloak 26.0.6). "
                      "Nothing is changed, nothing is imported.", out)
        self.assertEqual(self.lines(out, "differs") + self.lines(out, "only"), [], "ids and masked secrets are not differences")
        self.assertIn("0 differences;", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.state(), before)
        self.assertEqual([p for m, p in Keycloak.log if "/admin/realms/" in p and not p.startswith("/admin/realms/aap")], [])

    def test_a_field_a_thing_and_a_step_that_differ(self):
        doc = export_of(Keycloak.realms["aap"])
        doc["realm"] = "aap-site-a"
        doc["clients"][0]["redirectUris"] = ["https://aap.site-a.yoursite.mil/api/gateway/social/complete/*"]
        doc["clients"][0]["protocolMappers"] = [m for m in doc["clients"][0]["protocolMappers"] if m["name"] != "audience"]
        doc["groups"].append({"name": "site-a-only", "path": "/site-a-only"})
        fed = doc["components"][STORAGE][0]
        fed["name"] = "corp-ad"                        # another name there: still the one federation
        fed["config"]["changedSyncPeriod"] = ["300"]
        for f in doc["authenticationFlows"]:
            if f["alias"] == "browser-x509":
                f["authenticationExecutions"][0]["requirement"] = "DISABLED"
        self.export(doc)
        rc, out, err = self.run_it("--compare", self.path, **self.env)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("note    user federation corp-ad of the export is taken as user federation directory here", out)
        self.assertIn("differs client aap: redirectUris is https://aap.yoursite.mil/api/gateway/social/complete/* here, "
                      "the export says https://aap.site-a.yoursite.mil/api/gateway/social/complete/*", out)
        self.assertIn("differs user federation directory: changedSyncPeriod is 900 here, the export says 300", out)
        self.assertIn("only here           client aap: the mapper audience", out)
        self.assertIn("only in the export  group site-a-only", out)
        self.assertIn("differs the browser flow: its steps here, then the export's", out)
        self.assertNotIn("differs user federation directory: bindCredential", out)
        self.assertNotIn(LDAP_PASSWORD, out + err)
        self.assertNotIn(CLIENT_SECRET, out + err)
        self.assertIn("5 differences;", out)
        self.assertEqual(self.writes(), [])

    def test_it_goes_with_nothing_that_changes(self):
        self.export(export_of(Keycloak.realms["aap"]))
        for more in (("--apply",), ("--adopt",), ("--sync",), ("--update-existing",),
                     ("--secret-file", os.path.join(self.tmp, "never.secret"))):
            rc, out, err = self.run_it("--compare", self.path, *more, **self.env)
            self.assertEqual(rc, 2, more)
            self.assertIn("--compare only reads and prints", err)
        self.assertEqual(Keycloak.log, [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "never.secret")))

    def test_a_file_that_is_no_export(self):
        for text, said in (("{not json", "cannot be read as a realm export"), ('{"clients": []}', "is not a realm export"),
                           ('[1, 2]', "is not a realm export")):
            with open(self.path, "w") as f:
                f.write(text)
            rc, out, err = self.run_it("--compare", self.path, **self.env)
            self.assertEqual(rc, 2, text)
            self.assertIn(said, err)
        rc, out, err = self.run_it("--compare", os.path.join(self.tmp, "no-such.json"), **self.env)
        self.assertEqual(rc, 2)
        self.assertEqual(self.writes(), [])

    def test_a_realm_that_is_not_there(self):
        self.export(export_of(Keycloak.realms["aap"]))
        rc, out, err = self.run_it("--compare", self.path, KEYCLOAK_REALM="aap2")
        self.assertEqual(rc, 2, out + err)
        self.assertIn("The realm aap2 is not there: there is nothing to compare.", err)
        self.assertNotIn("aap2", Keycloak.realms, "a compare never makes the realm")


class NothingOutside(Base):
    """An air-gapped network: the script talks to Keycloak (and, for LDAP_CA_FILE, the LDAP server) and to nothing else."""

    NOWHERE = "http://192.0.2.1:9"

    def run_inside(self, args, **more):
        """The script in this process, a proxy in the environment that leads nowhere, and every name lookup written down."""
        mod, lines, asked = load(), [], []
        real = socket.getaddrinfo

        def lookup(host, *a, **kw):
            asked.append(host)
            if host != "127.0.0.1":
                raise socket.gaierror("no name service on this network")
            return real(host, *a, **kw)
        env = {"KEYCLOAK_URL": "https://keycloak.yoursite.mil", "AAP_URL": "https://aap.yoursite.mil",
               "KEYCLOAK_ADMIN_URL": self.url, "KEYCLOAK_ADMIN_PASSWORD_FILE": self.pwfile, "KEYCLOAK_TIMEOUT": "5"}
        env.update(more)
        proxies = {k: self.NOWHERE for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy")}
        proxies.update(no_proxy="", NO_PROXY="")
        with mock.patch.dict(os.environ, proxies), mock.patch.object(socket, "getaddrinfo", lookup):
            rc = mod.run(["--via", "rest"] + list(args), env, lines.append)
        return rc, "\n".join(lines), asked

    def test_a_proxy_in_the_environment_is_not_used_and_no_other_name_is_looked_up(self):
        for args in ((), ("--apply",), ("--apply", "--sync")):
            rc, out, asked = self.run_inside(args, **self.ldap())
            self.assertEqual(rc, 0, out)
            self.assertEqual(set(asked), {"127.0.0.1"}, "only Keycloak's admin address")
        self.assertIn("SYNC    a full sync of the directory", out)
        self.assertEqual(len(Keycloak.realms["aap"]["components"]), 2, "it did reach Keycloak")
        self.assertIn("to another realm: 0", out)

    def test_the_ldap_server_is_the_only_other_host_and_only_for_its_certificate(self):
        ca = os.path.join(self.tmp, "ldap-ca.pem")
        made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=Test CA",
                               "-keyout", os.path.join(self.tmp, "ldap-ca.key"), "-out", ca], capture_output=True)
        if made.returncode != 0:
            self.skipTest("openssl is not on this machine")
        rc, out, asked = self.run_inside((), LDAP_CA_FILE=ca, **self.ldap())
        self.assertEqual(rc, 0, out)
        self.assertEqual(set(asked), {"127.0.0.1", "dc01.corp.example.mil"})
        self.assertIn("dc01.corp.example.mil:636 is not reached from this machine", out)

    def test_compare_reads_a_file_and_keycloak(self):
        self.assertEqual(self.run_it("--apply")[0], 0)
        path = os.path.join(self.tmp, "export.json")
        with open(path, "w") as f:
            json.dump(export_of(Keycloak.realms["aap"]), f)
        rc, out, asked = self.run_inside(("--compare", path))
        self.assertEqual(rc, 0, out)
        self.assertEqual(set(asked), {"127.0.0.1"})

    def test_the_scripts_hold_no_outside_address(self):
        """No address is built in: every host a script talks to comes from the settings."""
        here = os.path.dirname(SCRIPT)
        for name in ("aap-keycloak-realm.py", "aap-nginx-blocks.py", "aap-keycloak-check.sh"):
            src = read(os.path.join(here, name))
            hosts = set(re.findall(r"(?:https?|ldaps?)://([A-Za-z0-9.-]+)", src))
            self.assertTrue(all(h.endswith(("yoursite.mil", "example.mil")) or h in ("127.0.0.1", "...") or "." not in h or
                                re.match(r"^192\.0\.2\.\d+$", h) for h in hosts), (name, hosts))
        blocks = read(os.path.join(here, "aap-nginx-blocks.py"))
        self.assertEqual(set(re.findall(r"(?m)^(?:import|from) ([a-z_.]+)", blocks)), {"os", "re", "sys"},
                         "the generator has nothing to make a request with")
        check = read(os.path.join(here, "aap-keycloak-check.sh"))
        self.assertIn("CURL=(curl --silent --http1.1 --noproxy '*'", check)
        calls = [ln for ln in check.splitlines() if re.search(r"(?:^|[|;&]|\$\(|=\()\s*curl\s", ln) and not ln.lstrip().startswith("#")]
        self.assertEqual([ln for ln in calls if "CURL=(curl" not in ln and "command -v curl" not in ln], [],
                         "curl is run only through CURL, which never takes a proxy")
        self.assertEqual(re.findall(r"(?m)(?:^|[|;&(])\s*(wget|nslookup|dig|host|nc|ping)\s", check), [],
                         "nothing else that would ask the network")
        self.assertEqual(re.findall(r"\(getent ([^|)]+?) *(?:2>|\|)", check), ['hosts "$name"'],
                         "a name is looked up only when it is one of the given hosts")
        self.assertNotIn("/dev/tcp", check)


class ThePassword(Base):

    def test_never_an_argument_and_never_printed(self):
        for args in ((PASSWORD,), ("--password", PASSWORD), ("--apply", "-p", PASSWORD)):
            rc, out, err = self.run_it(*args)
            self.assertEqual(rc, 2, args)
            self.assertIn("A password is never an argument.", err)
            self.assertEqual(Keycloak.log, [])
        src = read(SCRIPT)
        self.assertNotIn("--password", src.replace("-password-form", ""))
        rc, out, err = self.run_it("--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertNotIn(PASSWORD, out + err)
        self.assertNotIn(TOKEN, out + err)

    def test_wrong_password(self):
        bad = os.path.join(self.tmp, "bad.pw")
        with open(bad, "w") as f:
            f.write("not-it\n")
        rc, out, err = self.run_it("--apply", KEYCLOAK_ADMIN_PASSWORD_FILE=bad)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("did not accept the admin account admin", err)
        self.assertEqual(self.writes(), [])
        self.assertNotIn("not-it", out + err)

    def test_no_file_and_no_terminal(self):
        rc, out, err = self.run_it(KEYCLOAK_ADMIN_PASSWORD_FILE=None)
        self.assertEqual(rc, 2, out + err)
        self.assertIn("there is no terminal to ask at", err)
        self.assertEqual(Keycloak.log, [])

    def test_keycloak_does_not_answer(self):
        rc, out, err = self.run_it(KEYCLOAK_ADMIN_URL="http://127.0.0.1:1", KEYCLOAK_TIMEOUT="3")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("does not answer", err)


class TheKcadmWay(Base):
    """kcadm.sh inside the container, through a stand-in runtime that turns each call into a request to the fake."""

    def setUp(self):
        Keycloak.reset()
        self.bindir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bindir, exist_ok=True)
        self.argv_log = os.path.join(self.tmp, "runtime.log")
        if os.path.exists(self.argv_log):
            os.remove(self.argv_log)
        tool = os.path.join(self.bindir, "podman")
        with open(tool, "w") as f:
            f.write(RUNTIME.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        os.chmod(tool, 0o755)

    def kcadm(self, *args, **env):
        e = dict(PATH=self.bindir + os.pathsep + os.environ["PATH"], KEYCLOAK_ADMIN_URL="http://127.0.0.1:1",
                 KEYCLOAK_CONTAINER="stigman-keycloak", KEYCLOAK_RUNTIME="podman", FAKE_RUNTIME_LOG=self.argv_log,
                 FAKE_RUNTIME_URL=self.url, FAKE_RUNTIME_TOKEN=TOKEN, FAKE_RUNTIME_PASSWORD=PASSWORD,
                 FAKE_RUNTIME_CONTAINER="stigman-keycloak")
        e.update(env)
        return self.run_it("--via", "kcadm", *args, **e)

    def test_dry_run_apply_and_again(self):
        stigman = json.dumps(Keycloak.realms["stigman"], sort_keys=True)
        before = self.state()
        rc, out, err = self.kcadm()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("through kcadm (podman exec stigman-keycloak /opt/keycloak/bin/kcadm.sh)", out)
        self.assertIn("12 changes would be made.", out)
        self.assertEqual(self.state(), before)
        rc, out, err = self.kcadm("--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("12 changes made in the realm aap.", out)
        r = Keycloak.realms["aap"]
        self.assertEqual(r["rep"]["browserFlow"], "browser-x509")
        top = [e.get("providerId") or "flow" for e in r["flows"]["browser-x509"] if e["level"] == 0]
        self.assertEqual(top.index(X509), len(top) - 2)
        self.assertEqual(len(r["groups"]), 3)
        Keycloak.log = []
        rc, out, err = self.kcadm("--apply")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Nothing to change", out)
        self.assertEqual(self.writes(), [])
        self.assertEqual(json.dumps(Keycloak.realms["stigman"], sort_keys=True), stigman)

    def test_the_password_is_in_no_argument_list(self):
        rc, out, err = self.kcadm("--apply")
        self.assertEqual(rc, 0, out + err)
        calls = [json.loads(ln) for ln in read(self.argv_log).splitlines()]
        self.assertGreater(len(calls), 20)
        for argv in calls:
            self.assertNotIn(PASSWORD, " ".join(argv))
            self.assertNotIn("--password", argv)
        login = [a for a in calls if "credentials" in a]
        self.assertEqual(len(login), 1)
        self.assertIn("KC_CLI_PASSWORD", login[0], "handed over by name, through the environment")
        for argv in calls:
            if argv[0] == "exec" and any(x.endswith("kcadm.sh") for x in argv) and "credentials" not in argv:
                rel = argv[argv.index("--config") - 1]
                self.assertTrue(rel == "realms" or rel == "realms/aap" or rel.startswith("realms/aap/"), rel)
        self.assertEqual(calls[-1][-3:-1], ["rm", "-f"], "kcadm's session file is removed at the end")

    def test_wrong_password(self):
        rc, out, err = self.kcadm("--apply", FAKE_RUNTIME_PASSWORD="another")
        self.assertEqual(rc, 1, out + err)
        self.assertIn("did not accept the admin account", err)

    def test_auto_picks_kcadm_when_the_admin_api_is_closed(self):
        e = dict(PATH=self.bindir + os.pathsep + os.environ["PATH"], KEYCLOAK_ADMIN_URL="http://127.0.0.1:1",
                 KEYCLOAK_TIMEOUT="3", FAKE_RUNTIME_LOG=self.argv_log, FAKE_RUNTIME_URL=self.url, FAKE_RUNTIME_TOKEN=TOKEN,
                 FAKE_RUNTIME_PASSWORD=PASSWORD, FAKE_RUNTIME_CONTAINER="stigman-keycloak", KEYCLOAK_RUNTIME="podman")
        rc, out, err = self.run_it("--via", "auto", **e)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("through kcadm (podman exec stigman-keycloak", out, "the one container with keycloak in its name")


class TheGuard(unittest.TestCase):

    def test_only_the_realm_s_own_addresses(self):
        allowed = load().allowed
        for method, path in (("GET", "/admin/realms/aap"), ("PUT", "/admin/realms/aap"), ("POST", "/admin/realms"),
                             ("GET", "/admin/realms/aap/clients?clientId=aap"), ("POST", "/admin/realms/aap/groups")):
            self.assertTrue(allowed(method, path, "aap"), (method, path))
        for method, path in (("GET", "/admin/realms"), ("GET", "/admin/realms/stigman"), ("PUT", "/admin/realms/stigman"),
                             ("GET", "/admin/realms/aap2"), ("GET", "/admin/realms/aapx/clients"),
                             ("POST", "/admin/realms/master/clients"), ("GET", "/admin/realms/aap/../stigman"),
                             ("GET", "/admin/realms/aap/%2e%2e/stigman"), ("GET", "/admin/realms/aap/%2E%2E%2Fstigman"),
                             ("GET", "/admin/realms/aap//../stigman"), ("DELETE", "/admin/realms"),
                             ("GET", "/admin/serverinfo"), ("GET", "/realms/stigman"), ("PUT", "/admin/realms")):
            self.assertFalse(allowed(method, path, "aap"), (method, path))

    def test_a_request_outside_the_realm_stops_the_run(self):
        mod = load()
        sent = []

        class Door(object):
            def call(self, method, path, body=None):
                sent.append((method, path))
                return 200, {}, "{}"
        api = mod.Api(Door(), "aap", True, lambda line: None)
        self.assertEqual(api.get("/clients"), {})
        with self.assertRaises(mod.Stop):
            api._send("GET", "/admin/realms/stigman/clients")
        with self.assertRaises(mod.Stop):
            api.change("CHANGE", "x", "PUT", "/../stigman", {})
        self.assertEqual(sent, [("GET", "/admin/realms/aap/clients")])
        dry = mod.Api(Door(), "aap", False, lambda line: None)
        self.assertFalse(dry.change("CREATE", "x", "POST", "/groups", {}))
        with self.assertRaises(mod.Stop):
            dry.quiet("POST", "/groups", {})
        self.assertEqual(len(sent), 1, "a dry run sends no change")

    def test_names_that_would_climb_out_are_refused(self):
        mod = load()
        good = dict(mod.DEFAULTS, AAP_URL="https://aap.yoursite.mil", KEYCLOAK_URL="https://keycloak.yoursite.mil",
                    KEYCLOAK_ADMIN_URL="https://keycloak.yoursite.mil", X509_IDENTITY="cn",
                    AAP_GROUPS="aap-admins,aap-operators,aap-users")
        mod.check_settings(good)
        for k, v in (("X509_FLOW", "../../stigman/authentication"), ("KEYCLOAK_CLIENT_ID", "a/b"),
                     ("KEYCLOAK_REALM", "aap/../stigman"), ("KEYCLOAK_REALM", ""), ("AAP_URL", "http://aap.yoursite.mil"),
                     ("AAP_URL", "https://aap.yoursite.mil/aap"), ("AAP_REDIRECT_URI", "https://other.yoursite.mil/x/"),
                     ("X509_USER_ATTRIBUTE", "a/../b"), ("LDAP_GROUP_DN", "CN=AAP-Access,DC=corp,DC=example,DC=mil")):
            with self.assertRaises(mod.Stop, msg=(k, v)) as c:
                mod.check_settings(dict(good, **{k: v}))
            self.assertEqual(c.exception.code, 2)

    def test_it_never_deletes(self):
        src = read(SCRIPT)
        self.assertNotIn('"DELETE"', src)
        self.assertNotIn("'DELETE'", src)
        self.assertNotIn('"delete"', src)


class TheScript(unittest.TestCase):

    def test_standard_library_only_and_old_python(self):
        src = read(SCRIPT)
        mods = set(re.findall(r"(?m)^(?:import|from) ([a-z_.]+)", src))
        self.assertTrue(mods <= {"csv", "getpass", "json", "os", "re", "shutil", "socket", "ssl", "subprocess", "sys", "urllib.error",
                                 "urllib.parse", "urllib.request"}, mods)
        self.assertTrue(src.startswith("#!/usr/bin/env python3\n"))
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        self.assertNotIn("capture_output", src, "Python 3.6 has no capture_output")
        self.assertNotIn(":=", src, "nothing newer than Python 3.6")

    def test_help_names_every_setting(self):
        r = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        mod = load()
        for k in mod.DEFAULTS:
            self.assertIn(k, r.stdout, k)
        self.assertNotIn("@SETTINGS@", r.stdout)
        self.assertEqual(mod.DEFAULTS["KEYCLOAK_REALM"], "aap")
        self.assertEqual(mod.DEFAULTS["KEYCLOAK_PROTECTED_REALMS"], "master")

    def test_settings_file(self):
        mod = load()
        tmp = tempfile.mkdtemp(prefix="aapset")
        try:
            p = os.path.join(tmp, "s.env")
            with open(p, "w") as f:
                f.write("# a note\nAAP_URL=https://aap.yoursite.mil/\nexport KEYCLOAK_URL='https://keycloak.yoursite.mil'\n"
                        "KEYCLOAK_REALM=automation\nNGINX_LISTEN=443 ssl\nREPORT_WEB_FOLDER=/srv/site-reports\n")
            s = mod.read_settings(p, {"KEYCLOAK_REALM": "aap9", "REPORT_WEB_FOLDER": "/x"})
            self.assertEqual(s["AAP_URL"], "https://aap.yoursite.mil")
            self.assertEqual(s["KEYCLOAK_ADMIN_URL"], "https://keycloak.yoursite.mil")
            self.assertEqual(s["KEYCLOAK_REALM"], "aap9", "the environment wins over the file")
            self.assertNotIn("NGINX_LISTEN", s, "another script's setting is passed over")
            with open(p, "a") as f:
                f.write("KEYCLOAK_RELM=aap\n")
            with self.assertRaises(mod.Stop) as c:
                mod.read_settings(p, {})
            self.assertIn("line 7: not a setting: KEYCLOAK_RELM", str(c.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_example_file_is_read_by_the_script(self):
        mod = load()
        s = mod.read_settings(EXAMPLE, {})
        mod.check_settings(s)
        self.assertEqual(s["KEYCLOAK_REALM"], "aap")
        self.assertIn("stigman", s["KEYCLOAK_PROTECTED_REALMS"])
        text = read(EXAMPLE)
        self.assertFalse(re.search(r"(?mi)^[A-Z_]*(PASSWORD|SECRET|TOKEN)=", text), "no password in the settings file")
        for line in text.splitlines():
            m = re.match(r"#?([A-Z][A-Z0-9_]+)=", line)
            if m:
                self.assertTrue(m.group(1) in mod.DEFAULTS or m.group(1) in mod.OTHER, m.group(1))


class TheDoc(unittest.TestCase):

    def test_part_4_is_the_script(self):
        d = read(DOC)
        part = d[d.index("\n## 4. "):d.index("\n## 5. ")]
        self.assertIn("tools/aap-keycloak-realm.py --settings aap-keycloak.env\n", part)
        self.assertIn("tools/aap-keycloak-realm.py --settings aap-keycloak.env --apply", part)
        self.assertLess(part.index("tools/aap-keycloak-realm.py --settings aap-keycloak.env\n"),
                        part.index("tools/aap-keycloak-realm.py --settings aap-keycloak.env --apply"), "the dry run comes first")
        for said in ("It never touches another realm", "to another realm: 0", "aap-realm", "change you make by hand",
                     "--spi-x509cert-lookup-provider=nginx", "KC_SPI_X509CERT_LOOKUP_PROVIDER=nginx",
                     "--spi-x509cert-lookup-nginx-ssl-client-cert=ssl-client-cert", "restarts Keycloak",
                     "Nothing to change: the realm aap is as the settings say.", "AAP_USERS_FILE", "LDAP_URL",
                     "X509_IDENTITY", "KEYCLOAK_ADMIN_PASSWORD_FILE", "never typed on a command line"):
            self.assertIn(said, part, said)
        self.assertNotIn("Clients > Create client", part, "the manual realm steps are gone")

    def test_the_doc_shows_what_the_script_prints(self):
        """The dry run's lines in step 4.5 are the lines of a real dry run."""
        d = read(DOC)
        mod = load()
        lines = []
        Keycloak.reset()
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Keycloak)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        tmp = tempfile.mkdtemp(prefix="aapdoc")
        try:
            pw = os.path.join(tmp, "pw")
            with open(pw, "w") as f:
                f.write(PASSWORD + "\n")
            env = {"KEYCLOAK_URL": "https://keycloak.yoursite.mil", "AAP_URL": "https://aap.yoursite.mil",
                   "KEYCLOAK_ADMIN_URL": "http://127.0.0.1:%d" % httpd.server_address[1], "KEYCLOAK_ADMIN_PASSWORD_FILE": pw}
            self.assertEqual(mod.run(["--via", "rest"], env, lines.append), 0)
        finally:
            httpd.shutdown()
            httpd.server_close()
            shutil.rmtree(tmp, ignore_errors=True)
        shown = [ln for ln in lines[2:] if ln.strip() and not ln.startswith("Requests:")]
        self.assertGreater(len(shown), 12)
        for ln in shown:
            self.assertIn("   " + ln + "\n", d, ln)

    def test_every_setting_is_in_the_guide(self):
        d = read(DOC)
        section = d[d.index("### tools/aap-keycloak-realm.py"):d.index("### tools/aap-keycloak-check.sh")]
        for k in load().DEFAULTS:
            self.assertIn("`%s`" % k, section, k)


if __name__ == "__main__":
    unittest.main()
