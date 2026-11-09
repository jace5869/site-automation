#!/usr/bin/python
# -*- coding: utf-8 -*-
"""STIG Manager's health through its API, read-only: Keycloak (its OpenID configuration and a token for the client),
then each node and the address people use - the API's state and its database and Keycloak connections (/op/state),
its version and database schema (/op/configuration), an authenticated call that reads the database (/user,
/collections), and - with an admin client - the deployment's own numbers (/op/appinfo: MySQL's connections and uptime,
Node.js's memory and uptime). GET only, plus the token request. Runs on the AAP side. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stigman_health
short_description: STIG Manager's API, each node and Keycloak, read-only
description:
  - Never fails: what could not be read says why.
options:
  api: {description: "The address people use (https://stigman.example.mil/api; '' = none, the nodes only).", type: str, default: ''}
  nodes:
    description: "[{name, url: https://stig01.example.mil:54000/api}] - each node (an API server) on its own."
    type: list
    elements: dict
    default: []
  token_url: {description: "Keycloak's token endpoint ('' = no token: the unauthenticated checks only).", type: str, default: ''}
  client_id: {description: The client (client credentials)., type: str, default: ''}
  client_secret: {description: Its secret., type: str, default: '', no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  appinfo: {description: "Read /op/appinfo?elevate=true (the client needs stig-manager:op:read and admin in STIG Manager).", type: bool, default: true}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 20}
author: site automation
'''

RETURN = r'''
keycloak:
  description: "{url (the realm's), oidc: {status, ms, why}, token: {status, ms, why} (status 0 = not asked)}"
  type: dict
targets:
  description: "The address people use (kind service) then each node (kind node): {name, url, kind,
                state: {status, ms, why, currentState, since, db, oidc},
                configuration: {status, ms, why, version, sha, lastMigration, classification},
                user: {status, ms, why, username}, collections: {status, ms, why, count},
                appinfo: {status, ms, why, version, schema, mysql: {version, uptime, threads_connected, max_used_connections,
                          max_connections, aborted_connects}, nodejs: {version, uptime, rss, heap_used, heap_total}}}
                (status 0 = not asked, -1 = no answer)"
  type: list
'''

import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule


def reason(e):
    if isinstance(e, HTTPError):
        return "HTTP %d" % e.code
    r = getattr(e, "reason", e)
    if isinstance(r, ssl.SSLCertVerificationError):
        return "its certificate is not trusted (%s)" % (r.verify_message or r)
    if isinstance(r, (socket.timeout, TimeoutError)):
        return "no answer in time"
    if isinstance(r, ConnectionRefusedError):
        return "connection refused (nothing listens there)"
    if isinstance(r, socket.gaierror):
        return "the name does not resolve"
    return str(r)[:200]


class Client(object):
    def __init__(self, validate, ca_path, timeout, opener=None):
        if not validate:
            ctx = ssl._create_unverified_context()   # noqa: S323 - the settings say so
        else:
            ctx = ssl.create_default_context(cafile=ca_path) if ca_path else ssl.create_default_context()
        self.opener = opener or build_opener(HTTPSHandler(context=ctx))
        self.timeout = timeout

    def call(self, url, token="", data=None):
        """{status, ms, why, json}: status -1 = no answer."""
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=data, headers=headers)
        start = time.time()
        out = {"status": -1, "why": "", "json": None}
        try:
            r = self.opener.open(req, timeout=self.timeout)
            out["status"], body = r.status, r.read(8388608)
        except HTTPError as e:
            out["status"], out["why"] = e.code, reason(e)
            try:
                body = e.read(65536)
            except Exception:                             # noqa: BLE001 - the status says enough
                body = b""
        except (URLError, OSError, ValueError) as e:
            out["why"], body = reason(e), b""
        out["ms"] = int(round((time.time() - start) * 1000))
        if body:
            try:
                out["json"] = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                if out["status"] == 200:
                    out["status"], out["why"] = -2, "the answer is not JSON (not STIG Manager's API, or a proxy's page)"
        if out["status"] not in (200, -1) and not out["why"]:
            out["why"] = "HTTP %d" % out["status"]
        if out["status"] >= 400 and isinstance(out["json"], dict):
            detail = out["json"].get("error") or out["json"].get("error_description") or out["json"].get("detail")
            if out["json"].get("currentState"):
                detail = "the API is %s (database %s, Keycloak %s)" % (
                    out["json"]["currentState"], _up((out["json"].get("dependencies") or {}).get("db")),
                    _up((out["json"].get("dependencies") or {}).get("oidc")))
            if detail:
                out["why"] = "%s: %s" % (out["why"], str(detail)[:200])
        return out


def _up(v):
    return "up" if v is True else ("DOWN" if v is False else "?")


def strip(r, **keep):
    o = {"status": r["status"], "ms": r.get("ms"), "why": r["why"]}
    o.update(keep)
    return o


def keycloak(c, token_url, client_id, secret, scope):
    realm = token_url.split("/protocol/openid-connect/")[0] if "/protocol/openid-connect/" in token_url else ""
    out = {"url": realm, "oidc": {"status": 0, "ms": None, "why": "not asked"}, "token": {"status": 0, "ms": None, "why": "not asked"}}
    if realm:
        r = c.call(realm + "/.well-known/openid-configuration")
        out["oidc"] = strip(r)
    tok = ""
    if token_url and client_id:
        form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}
        if scope:
            form["scope"] = scope
        r = c.call(token_url, data=urlencode(form).encode())
        tok = (r["json"] or {}).get("access_token", "") if r["status"] == 200 and isinstance(r["json"], dict) else ""
        if r["status"] == 200 and not tok:
            r["status"], r["why"] = -2, "Keycloak answered without an access_token"
        out["token"] = strip(r)
    return out, tok


def pick(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def target(c, name, url, kind, token, appinfo):
    url = url.rstrip("/")
    t = {"name": name, "url": url, "kind": kind}
    r = c.call(url + "/op/state")
    j = r["json"] if isinstance(r["json"], dict) else {}
    deps = j.get("dependencies") or {}
    t["state"] = strip(r, currentState=j.get("currentState"), since=j.get("since"), db=deps.get("db"), oidc=deps.get("oidc"))
    if r["status"] == 503 and j.get("currentState"):
        t["state"]["status"] = 200                     # it answered with its state: the state says what is wrong
    r = c.call(url + "/op/configuration")
    j = r["json"] if isinstance(r["json"], dict) else {}
    t["configuration"] = strip(r, version=j.get("version"), sha=pick(j, "commit", "sha"), lastMigration=j.get("lastMigration"),
                               classification=j.get("classification"))
    not_asked = {"status": 0, "ms": None, "why": "no token"}
    t["user"], t["collections"], t["appinfo"] = dict(not_asked), dict(not_asked), dict(not_asked)
    if not token:
        return t
    r = c.call(url + "/user", token)
    t["user"] = strip(r, username=pick(r["json"], "username"))
    r = c.call(url + "/collections", token)
    t["collections"] = strip(r, count=len(r["json"]) if isinstance(r["json"], list) else None)
    if not appinfo:
        t["appinfo"] = {"status": 0, "ms": None, "why": "not asked (stigman_health_appinfo)"}
        return t
    r = c.call(url + "/op/appinfo?elevate=true", token)
    if r["status"] == 404:                             # before 1.5: /op/details
        r = c.call(url + "/op/details?elevate=true", token)
    j = r["json"] if isinstance(r["json"], dict) else {}
    st, var = pick(j, "mysql", "status") or {}, pick(j, "mysql", "variables") or {}
    mem = pick(j, "nodejs", "memory") or {}
    if r["status"] == 403:
        r["why"] += " - the client needs the scope stig-manager:op:read and admin in STIG Manager (or stigman_health_appinfo: false)"
    t["appinfo"] = strip(r, version=j.get("version"), schema=j.get("schema"),
                         mysql={"version": pick(j, "mysql", "version"), "uptime": num(st.get("Uptime")),
                                "threads_connected": num(st.get("Threads_connected")),
                                "max_used_connections": num(st.get("Max_used_connections")),
                                "max_connections": num(var.get("max_connections")),
                                "aborted_connects": num(st.get("Aborted_connects"))},
                         nodejs={"version": pick(j, "nodejs", "version"), "uptime": num(pick(j, "nodejs", "uptime")),
                                 "rss": num(mem.get("rss")), "heap_used": num(mem.get("heapUsed")),
                                 "heap_total": num(mem.get("heapTotal"))})
    return t


def run(p, client=None):
    c = client or Client(p["validate_certs"], p["ca_path"], p["timeout"])
    kc, token = keycloak(c, p["token_url"], p["client_id"], p["client_secret"], p["scope"])
    targets = []
    if p["api"]:
        targets.append(target(c, "service", p["api"], "service", token, p["appinfo"]))
    for n in p["nodes"] or []:
        targets.append(target(c, n.get("name") or n["url"], n["url"], "node", token, p["appinfo"]))
    return kc, targets


def main():
    module = AnsibleModule(argument_spec=dict(api=dict(type="str", default=""), nodes=dict(type="list", elements="dict", default=[]),
                                              token_url=dict(type="str", default=""), client_id=dict(type="str", default=""),
                                              client_secret=dict(type="str", default="", no_log=True), scope=dict(type="str", default=""),
                                              appinfo=dict(type="bool", default=True), validate_certs=dict(type="bool", default=True),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=20)),
                           supports_check_mode=True)
    if not module.params["api"] and not module.params["nodes"]:
        module.fail_json(msg="Nothing to check: set stigman_health_api (the address people use) or stigman_health_nodes (each node), "
                             "or put the nodes in the inventory group stigman")
    kc, targets = run(module.params)
    module.exit_json(changed=False, keycloak=kc, targets=targets,
                     msg="; ".join("%s %s" % (t["name"], t["state"].get("currentState") or t["state"]["why"] or t["state"]["status"])
                                   for t in targets))


if __name__ == "__main__":
    main()
