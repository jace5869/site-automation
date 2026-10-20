#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read VMware Horizon 8 and act on its desktops and sessions through the Connection Server's REST API
(https://<connection-server>/rest/). The address and login come from the "Horizon API" credential's
variables. Standard library only. The API version of each path is found by asking: the newest first,
down to v1 - so the same job works on any Horizon 8 release."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_horizon
short_description: Read Horizon 8 (pools, desktops, sessions, health) and act on desktops and sessions
description:
  - C(read) reads the resources named (see RESOURCES in the code), every page of each. Per resource it asks
    the newest API version first (I(newest)) and goes down to v1: the first that answers is used, and
    returned in C(api). A resource no version has (HTTP 404 at every one) is in C(errors), never a failure.
  - C(test) asks every resource it knows, one page each, and says which path answered, how many records,
    or why not - the first look at a new Horizon (playbooks/horizon_api_test.yml).
  - C(act) runs one action on a list of IDs - machines (reset, restart, recover, rebuild, remove,
    enter-maintenance-mode, exit-maintenance-mode), sessions (logoff, disconnect, reset, restart,
    send-message), or a pool's image push (push-image). In check mode nothing is sent: each ID says what
    would happen.
  - Horizon's address and login come from the environment, as the "Horizon API" credential sets them -
    HORIZON_URL, HORIZON_DOMAIN, HORIZON_USERNAME, HORIZON_PASSWORD, HORIZON_VERIFY_SSL. A user name
    written DOMAIN\\user or user@domain needs no domain. It logs out at the end.
options:
  action: {description: read, test or act., type: str, choices: [read, test, act], default: read}
  resources:
    description: "C(read): the resources to read - names, or {name, key, query, id} (query - URL parameters,
      e.g. {vcenter_id: X}; id - one record by its ID; key - the name the answer gets, default the name)."
    type: list
    elements: raw
    default: []
  target: {description: "C(act): machines, sessions or pool.", type: str, default: machines}
  verb: {description: "C(act): the action (see above).", type: str, default: ''}
  ids: {description: "C(act): the machine, session or pool IDs.", type: list, elements: str, default: []}
  body: {description: "C(act): more of the request - send-message: {message, message_type}; push-image: its settings; remove: machine_delete_data.", type: dict, default: {}}
  query: {description: "C(act): URL parameters, e.g. {forced_logoff: true} for logoff.", type: dict, default: {}}
  newest: {description: The newest API version to try (vN down to v1)., type: int, default: 9}
  action_version: {description: The API version of the actions., type: str, default: v1}
  page_size: {description: Records per page., type: int, default: 500}
  ca_path: {description: A CA file for the Connection Server's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
'''

RETURN = r'''
data: {description: "C(read): {key: [records]} - a resource with one record (event database, license) is a list of one.", type: dict}
api: {description: "The path that answered, per key (e.g. rest/monitor/v3/connection-servers).", type: dict}
errors: {description: "Why a key could not be read: {key: reason}.", type: dict}
tests: {description: "C(test): [{name, path, status, count, more, error}].", type: list}
results: {description: "C(act): [{id, ok, status, error}] - status: done, would, refused.", type: list}
user: {description: The login that was used (DOMAIN\\user)., type: str}
'''

import json
import os
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ansible.module_utils.basic import AnsibleModule

VARS = {"url": ("HORIZON_URL", "HORIZON_SERVER", "HORIZON_HOST"), "domain": ("HORIZON_DOMAIN",),
        "user": ("HORIZON_USERNAME", "HORIZON_USER"), "password": ("HORIZON_PASSWORD",),
        "verify": ("HORIZON_VERIFY_SSL", "HORIZON_VALIDATE_CERTS")}
NO_CREDENTIAL = ('No Horizon connection: attach a credential of type "Horizon API" to this job template (the Connection '
                 'Server\'s address, and the domain, user name and password of an account with a Horizon administrator role): '
                 'docs/HORIZON.md')
# name -> the candidate paths (area, path) - the first that answers at some version is used; single = one record, not a list
RESOURCES = {
    "connection_servers": [("monitor", "connection-servers")],
    "gateways": [("monitor", "gateways")],
    "virtual_centers": [("monitor", "virtual-centers")],
    "ad_domains": [("monitor", "ad-domains")],
    "event_database": [("monitor", "event-database")],
    "saml_authenticators": [("monitor", "saml-authenticators")],
    "true_sso": [("monitor", "true-sso")],
    "license": [("config", "licenses"), ("config", "license"), ("monitor", "licenses")],
    "desktop_pools": [("inventory", "desktop-pools")],
    "machines": [("inventory", "machines")],
    "sessions": [("inventory", "sessions")],
    "farms": [("inventory", "farms")],
    "vcenters": [("config", "virtual-centers")],
    "base_vms": [("external", "base-vms")],
    "base_snapshots": [("external", "base-snapshots")],
    "ad_users": [("external", "ad-users-or-groups")],
}
# asked one page each by `test` (the external ones need IDs: the jobs read them)
TESTED = ["connection_servers", "gateways", "virtual_centers", "ad_domains", "event_database", "saml_authenticators", "true_sso",
          "license", "desktop_pools", "machines", "sessions", "farms", "vcenters"]
MACHINE_VERBS = ("reset", "restart", "recover", "rebuild", "remove", "enter-maintenance-mode", "exit-maintenance-mode")
SESSION_VERBS = ("logoff", "disconnect", "reset", "restart", "send-message")
NOT_HERE = (404, 405)


def connection(env=None):
    """Horizon's address and login from the credential's variables (the first name set of each)."""
    env = os.environ if env is None else env
    c = {}
    for k, names in VARS.items():
        c[k] = next((str(env.get(n)).strip() for n in names if str(env.get(n) or "").strip()), "")
    u = c["url"]
    if u:
        u = (u if u.startswith("http") else "https://" + u).rstrip("/")
        if u.endswith("/rest"):
            u = u[:-5]
    c["url"] = u
    c["verify"] = c["verify"].lower() not in ("0", "false", "no", "off") if c["verify"] else True
    user, dom = c["user"], c["domain"]
    if "\\" in user:
        dom, user = user.split("\\", 1)
    elif "@" in user and not dom:
        user, dom = user.split("@", 1)
    c["user"], c["domain"] = user, dom
    return c


def error_text(data):
    """Horizon's reason, from its JSON answer: {error_message} or {errors: [{error_message}]}."""
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {"detail": str(data)[:200]}
    if not isinstance(data, dict):
        return str(data or "")[:200]
    msgs = []
    for e in data.get("errors") or []:
        if isinstance(e, dict):
            msgs.append(str(e.get("error_message") or e.get("message") or e.get("error_key") or e))
    for k in ("error_message", "message", "detail", "error"):
        if data.get(k) and not msgs:
            msgs.append(str(data[k]))
    return "; ".join(msgs)[:300] or ""


def bulk_results(ids, code, data, verb):
    """One result per ID from a bulk action's answer: [{id, status_code, errors}] - or the whole answer for all."""
    if code < 0 or code >= 300:
        why = "HTTP %d: %s" % (code, error_text(data) or "no reason given") if code > 0 else error_text(data)
        return [{"id": i, "ok": False, "status": "refused", "error": why} for i in ids]
    rows = data if isinstance(data, list) else (data.get("results") if isinstance(data, dict) else None)
    by = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get("id") is not None:
            by[str(r["id"])] = r
    out = []
    for i in ids:
        r = by.get(str(i))
        if r is None:
            out.append({"id": i, "ok": True, "status": "done", "error": ""})
            continue
        sc = r.get("status_code")
        try:
            sc = int(sc) if sc is not None else 200
        except (TypeError, ValueError):
            sc = 200
        why = error_text(r) if (r.get("errors") or r.get("error_message")) else ""
        ok = sc < 300 and not why
        out.append({"id": i, "ok": ok, "status": "done" if ok else "refused",
                    "error": "" if ok else ("%s refused (HTTP %d): %s" % (verb, sc, why or "no reason given"))})
    return out


class Api(object):
    def __init__(self, conn, ca_path="", timeout=60, newest=9, page_size=500):
        self.conn, self.timeout, self.newest, self.size = conn, timeout, max(1, newest), max(1, min(page_size, 1000))
        self.token, self.refresh, self.found, self.ca_error = "", "", {}, ""
        if ca_path and conn["verify"] and not os.path.isfile(ca_path):
            self.ca_error = "the CA file %s (horizon_ca_path) is not there" % ca_path
            ca_path = ""
        if not conn["verify"]:
            self.ctx = ssl._create_unverified_context()   # noqa: S323 - the credential says so
        elif ca_path:
            self.ctx = ssl.create_default_context(cafile=ca_path)
        else:
            self.ctx = ssl.create_default_context()

    def raw(self, method, path, body=None, auth=True):
        """(HTTP status, JSON answer, headers); status -1 = no answer (the address, a firewall, the certificate)."""
        req = Request(self.conn["url"] + "/" + path, data=json.dumps(body).encode() if body is not None else None)
        req.get_method = lambda: method
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if auth and self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        try:
            r = urlopen(req, timeout=self.timeout, context=self.ctx if self.conn["url"].startswith("https") else None)
            raw = r.read()
            headers = dict((k.upper(), v) for k, v in r.headers.items())
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return -1, {"error_message": "the answer to %s %s is not Horizon's (HTTP %d, not JSON): the credential's URL must be "
                                             "a Connection Server's address" % (method, path.split("?")[0], r.getcode())}, headers
            return r.getcode(), data, headers
        except HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}"), {}
            except ValueError:
                return e.code, {"error_message": "HTTP %d" % e.code}, {}
            finally:
                e.close()
        except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
            return -1, {"error_message": str(getattr(e, "reason", e))}, {}

    def call(self, method, path, body=None):
        """A call with the login; an expired access token is refreshed once."""
        code, data, headers = self.raw(method, path, body)
        if code == 401 and self.refresh:
            c2, d2, _ = self.raw("POST", "rest/refresh", {"refresh_token": self.refresh}, auth=False)
            if c2 == 200 and isinstance(d2, dict) and d2.get("access_token"):
                self.token = d2["access_token"]
                code, data, headers = self.raw(method, path, body)
        return code, data, headers

    def login(self):
        """'' or why not."""
        c = self.conn
        if not c["url"] or not c["user"] or not c["password"]:
            return NO_CREDENTIAL
        if not c["domain"]:
            return 'No domain for the Horizon login: fill in the credential\'s domain (e.g. CORP), or write the user name as DOMAIN\\user'
        if self.ca_error:
            return self.ca_error
        code, data, _ = self.raw("POST", "rest/login", {"domain": c["domain"], "username": c["user"], "password": c["password"]}, auth=False)
        if code == 200 and isinstance(data, dict) and data.get("access_token"):
            self.token, self.refresh = data["access_token"], data.get("refresh_token") or ""
            return ""
        if code < 0:
            return ("Horizon did not answer at %s: %s (the address, a firewall - port 443 from the execution environment - or "
                    "its certificate: horizon_ca_path)" % (c["url"], error_text(data)))
        if code in (400, 401, 403):
            return ("Horizon refused the login of %s\\%s (HTTP %d%s): the user name, password or domain is wrong, or the account "
                    "has no Horizon administrator role (Horizon Console > Settings > Administrators)"
                    % (c["domain"], c["user"], code, (": " + error_text(data)) if error_text(data) else ""))
        if code == 404:
            return "Horizon has no REST API at %s/rest/ (HTTP 404): the credential's URL must be a Connection Server's address" % c["url"]
        return "Horizon answered the login with HTTP %d: %s" % (code, error_text(data))

    def logout(self):
        if self.refresh:
            self.raw("POST", "rest/logout", {"refresh_token": self.refresh})

    def versions(self):
        return ["v%d" % n for n in range(self.newest, 0, -1)] + [""]

    def find(self, name, probe_query=""):
        """The path of a resource that answers (rest/area/vN/path), asked newest first; (path, '') or ('', why)."""
        if name in self.found:
            return self.found[name]
        for area, path in RESOURCES[name]:
            for v in self.versions():
                p = "rest/%s/%s%s" % (area, (v + "/") if v else "", path)
                code, data, _ = self.call("GET", p + probe_query)
                if code in NOT_HERE:
                    continue
                # 400: the path is there, the question is not what it wants (the read says why)
                self.found[name] = (p, "") if 200 <= code < 300 or code == 400 else ("", why_not(code, data, p))
                return self.found[name]
        self.found[name] = ("", "not in this Horizon (HTTP 404 at every API version, v%d to v1)" % self.newest)
        return self.found[name]

    def get_all(self, path, query=None, one=False, max_pages=200):
        """Every page of a list (page, size, HAS_MORE_RECORDS); ([records], '') or ([], why)."""
        out, page = [], 1
        while page <= max_pages:
            q = dict(query or {})
            if not one:
                q.update({"page": page, "size": self.size})
            code, data, headers = self.call("GET", path + ("?" + urlencode(q) if q else ""))
            if code >= 300 or code < 0:
                return out, why_not(code, data, path)
            if isinstance(data, dict) and not one and isinstance(data.get("results"), list):
                data = data["results"]
            if not isinstance(data, list):
                return out + ([data] if isinstance(data, dict) and data else []), ""
            out += data
            more = str(headers.get("HAS_MORE_RECORDS", "")).strip().lower() == "true"
            if not more or not data or one:
                return out, ""
            page += 1
        return out, "more than %d pages: only the first %d records were read" % (max_pages, len(out))


def why_not(code, data, path):
    if code < 0:
        return "no answer: %s" % error_text(data)
    if code == 401:
        return "the login was refused at %s (HTTP 401): the session ended, or the account may not read it" % path
    if code == 403:
        return "the account may not read %s (HTTP 403): give its Horizon role the privileges in docs/HORIZON.md" % path
    return "%s answered HTTP %d%s" % (path, code, (": " + error_text(data)) if error_text(data) else "")


def read(api, resources):
    data, paths, errors = {}, {}, {}
    for r in resources:
        r = {"name": r} if not isinstance(r, dict) else r
        name = str(r.get("name") or "")
        key = str(r.get("key") or name)
        if name not in RESOURCES:
            errors[key] = "unknown resource %s (known: %s)" % (name, ", ".join(sorted(RESOURCES)))
            continue
        query = dict((k, v) for k, v in (r.get("query") or {}).items() if v not in (None, ""))
        probe = ("?" + urlencode(query)) if query else ""
        if r.get("id"):
            probe = ""
        path, why = api.find(name, probe)
        if why:
            errors[key] = why
            data[key] = []
            continue
        one = bool(r.get("id")) or name in ("event_database", "license")
        rows, why = api.get_all(path + (("/" + quote(str(r["id"]), safe="")) if r.get("id") else ""), query, one=one)
        data[key], paths[key] = rows, path
        if why:
            errors[key] = why
    return data, paths, errors


def test(api):
    out = []
    for name in TESTED:
        path, why = api.find(name)
        t = {"name": name, "path": path, "status": "missing" if why.startswith("not in this") else ("error" if why else "ok"),
             "count": 0, "more": False, "error": why}
        if path:
            one = name in ("event_database", "license")
            code, data, headers = api.call("GET", path + ("" if one else "?" + urlencode({"page": 1, "size": api.size})))
            if 200 <= code < 300:
                rows = data.get("results") if isinstance(data, dict) and isinstance(data.get("results"), list) else data
                t["count"] = len(rows) if isinstance(rows, list) else (1 if rows else 0)
                t["more"] = str(headers.get("HAS_MORE_RECORDS", "")).strip().lower() == "true"
            else:
                t["status"], t["error"] = "error", why_not(code, data, path)
        out.append(t)
    return out


def act(api, target, verb, ids, body, query, version, check_mode):
    ids = [str(i) for i in ids if str(i).strip()]
    if not ids:
        return []
    v = version.strip("/") or "v1"
    if target == "machines" and verb not in MACHINE_VERBS:
        raise ValueError("machines: %s is not one of %s" % (verb, ", ".join(MACHINE_VERBS)))
    if target == "sessions" and verb not in SESSION_VERBS:
        raise ValueError("sessions: %s is not one of %s" % (verb, ", ".join(SESSION_VERBS)))
    if target == "pool" and verb != "push-image":
        raise ValueError("pool: the only action is push-image")
    if target not in ("machines", "sessions", "pool"):
        raise ValueError("target %s: machines, sessions or pool" % target)
    if check_mode:
        return [{"id": i, "ok": True, "status": "would", "error": ""} for i in ids]
    q = ("?" + urlencode(dict((k, str(x).lower() if isinstance(x, bool) else x) for k, x in (query or {}).items()))) if query else ""
    if target == "pool":
        out = []
        for i in ids:
            code, data, _ = api.call("POST", "rest/inventory/%s/desktop-pools/%s/action/schedule-push-image%s" % (v, quote(i, safe=""), q), body)
            ok = 200 <= code < 300
            out.append({"id": i, "ok": ok, "status": "done" if ok else "refused",
                        "error": "" if ok else "the push was refused (%s)" % why_not(code, data, "schedule-push-image")})
        return out
    if target == "machines" and verb == "remove":
        mdd = {"delete_from_disk": True, "archive_persistent_disk": False, "force_logoff_session": False,
               "allow_delete_from_multi_desktop_pools": False}
        mdd.update(body or {})
        code, data, _ = api.call("DELETE", "rest/inventory/%s/machines%s" % (v, q), {"machine_ids": ids, "machine_delete_data": mdd})
        return bulk_results(ids, code, data, verb)
    if target == "sessions" and verb == "send-message":
        b = {"message": "", "message_type": "INFO"}
        b.update(body or {})
        b["session_ids"] = ids
        code, data, _ = api.call("POST", "rest/inventory/%s/sessions/action/send-message%s" % (v, q), b)
        return bulk_results(ids, code, data, verb)
    code, data, _ = api.call("POST", "rest/inventory/%s/%s/action/%s%s" % (v, target, verb, q), ids)
    return bulk_results(ids, code, data, verb)


def main():
    module = AnsibleModule(argument_spec=dict(action=dict(type="str", choices=["read", "test", "act"], default="read"),
                                              resources=dict(type="list", elements="raw", default=[]),
                                              target=dict(type="str", default="machines"), verb=dict(type="str", default=""),
                                              ids=dict(type="list", elements="str", default=[]),
                                              body=dict(type="dict", default={}), query=dict(type="dict", default={}),
                                              newest=dict(type="int", default=9), action_version=dict(type="str", default="v1"),
                                              page_size=dict(type="int", default=500),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=60)),
                           supports_check_mode=True)
    p = module.params
    conn = connection()
    api = Api(conn, p["ca_path"], p["timeout"], p["newest"], p["page_size"])
    who = ("%s\\%s" % (conn["domain"], conn["user"])) if conn["user"] else ""
    if p["action"] == "act" and module.check_mode:
        # a dry run sends nothing - not even the login
        try:
            res = act(api, p["target"], p["verb"], p["ids"], p["body"], p["query"], p["action_version"], True)
        except ValueError as e:
            module.fail_json(msg=str(e))
        module.exit_json(changed=False, results=res, user=who, url=conn["url"])
    why = api.login()
    if why:
        if p["action"] == "test":
            module.exit_json(changed=False, login=why, tests=[], user=who, url=conn["url"])
        module.fail_json(msg=why, user=who, url=conn["url"])
    try:
        if p["action"] == "test":
            module.exit_json(changed=False, login="", tests=test(api), user=who, url=conn["url"])
        if p["action"] == "read":
            data, paths, errors = read(api, p["resources"])
            module.exit_json(changed=False, data=data, api=paths, errors=errors, user=who, url=conn["url"])
        try:
            res = act(api, p["target"], p["verb"], p["ids"], p["body"], p["query"], p["action_version"], False)
        except ValueError as e:
            module.fail_json(msg=str(e))
        module.exit_json(changed=any(r["ok"] for r in res), results=res, user=who, url=conn["url"])
    finally:
        api.logout()


if __name__ == "__main__":
    main()
