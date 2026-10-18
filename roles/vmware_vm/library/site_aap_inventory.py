#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Check AAP inventories and groups, and add hosts to them, through AAP's own API: the VMs the
deploy built join the inventory and group of their service (vm_deploy_services). The address and
login come from the "Red Hat Ansible Automation Platform" credential's variables. Standard library
only. Never creates an inventory or a group: a name that is not there is refused, never guessed."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_inventory
short_description: Check AAP inventories and groups; add hosts to them
description:
  - C(check) reads each target inventory - it must be a regular one (a smart or constructed inventory
    takes no hosts) that the AAP user may change - and its group, and the inventory's sources (a source
    with Overwrite on may take a host added here out of the groups it fills, at its next sync).
  - C(add) adds each host to its inventory (when not there yet) and to its group (when not in it yet).
    In check mode it only reads, and says what it would do. A host it cannot add is reported, never a
    failure (the VM is built either way).
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform"
    credential sets it - CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and
    CONTROLLER_PASSWORD, CONTROLLER_VERIFY_SSL (or the AAP_* / TOWER_* names). The API path is found
    by asking - api/controller/v2/ (AAP 2.5 and later), else api/v2/.
options:
  action: {description: check or add., type: str, choices: [check, add], default: check}
  targets: {description: "The inventories (and groups) to check: [{inventory: name or ID, group: name or empty}].", type: list, elements: dict, default: []}
  hosts: {description: "The hosts to add: [{name, vm, inventory, group, description, variables}] (vm - the VM's name, returned as it is; variables - a dict, set only on a host the module adds).", type: list, elements: dict, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
'''

RETURN = r'''
api: {description: The API path that answered., type: str, sample: api/controller/v2/}
problems:
  description: Why a target cannot take hosts, or AAP could not be reached - with C(check), stop before anything is built.
  type: list
notes:
  description: Things to know about a target that can take hosts (a source with Overwrite on).
  type: list
hosts:
  description: Each host added (C(add)) - what happened in its inventory and in its group, and an error when it failed.
  type: list
  sample: [{name: app01, vm: APP01, inventory: Linux servers, group: web, host: added, in_group: joined, ok: true, error: ""}]
'''

import base64
import json
import os
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ansible.module_utils.basic import AnsibleModule

# the credential's variables, by their NAMES: CONTROLLER_* (AAP 2.4 and later), AAP_* (2.5 and later), TOWER_* (older)
VARS = {"host": ("CONTROLLER_HOST", "AAP_HOSTNAME", "TOWER_HOST"),
        "token": ("CONTROLLER_OAUTH_TOKEN", "AAP_TOKEN", "TOWER_OAUTH_TOKEN"),
        "user": ("CONTROLLER_USERNAME", "AAP_USERNAME", "TOWER_USERNAME"),
        "password": ("CONTROLLER_PASSWORD", "AAP_PASSWORD", "TOWER_PASSWORD"),
        "verify": ("CONTROLLER_VERIFY_SSL", "AAP_VALIDATE_CERTS", "TOWER_VERIFY_SSL")}
NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the inventory)')


def connection(env=None):
    """AAP's address and login from the credential's variables (the first name set of each)."""
    env = os.environ if env is None else env
    c = {}
    for k, names in VARS.items():
        c[k] = next((str(env.get(n)).strip() for n in names if str(env.get(n) or "").strip()), "")
    h = c["host"]
    c["host"] = ((h if h.startswith("http") else "https://" + h).rstrip("/")) if h else ""
    c["verify"] = c["verify"].lower() not in ("0", "false", "no", "off") if c["verify"] else True
    c["login"] = "token" if c["token"] else ("password" if c["user"] and c["password"] else "")
    return c


def error_text(data):
    """AAP's reason, from its JSON answer: {"detail": ...} or {"field": ["reason"]}."""
    if not isinstance(data, dict):
        return str(data or "")[:200]
    if data.get("detail"):
        return str(data["detail"])
    parts = []
    for k, v in sorted(data.items()):
        v = v[0] if isinstance(v, list) and v else v
        parts.append("%s: %s" % (k, v))
    return "; ".join(parts)[:300]


class Api(object):
    def __init__(self, conn, ca_path="", timeout=30):
        self.conn, self.ca_path, self.timeout, self.base, self.ca_error = conn, ca_path, timeout, "", ""
        if ca_path and conn["verify"] and not os.path.isfile(ca_path):
            self.ca_error = "the CA file %s (vm_deploy_aap_ca_path) is not there" % ca_path
            ca_path = ""
        if not conn["verify"]:
            self.ctx = ssl._create_unverified_context()   # noqa: S323 - the credential says so
        elif ca_path:
            self.ctx = ssl.create_default_context(cafile=ca_path)
        else:
            self.ctx = ssl.create_default_context()

    def call(self, method, path, body=None):
        """(HTTP status, JSON answer); status -1 = no answer (the address, a firewall, the certificate)."""
        req = Request(self.conn["host"] + "/" + self.base + path, data=json.dumps(body).encode() if body is not None else None)
        req.get_method = lambda: method
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.conn["login"] == "token":
            req.add_header("Authorization", "Bearer " + self.conn["token"])
        else:
            pair = ("%s:%s" % (self.conn["user"], self.conn["password"])).encode()
            req.add_header("Authorization", "Basic " + base64.b64encode(pair).decode())
        try:
            r = urlopen(req, timeout=self.timeout, context=self.ctx if self.conn["host"].startswith("https") else None)
            raw = r.read()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return -1, {"detail": "the answer to %s %s is not AAP's (HTTP %d, not JSON): the credential's URL must be AAP's own address"
                                      % (method, path.split("?")[0], r.getcode())}
            return r.getcode(), data if isinstance(data, dict) else {"results": data}
        except HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"detail": "HTTP %d" % e.code}
            finally:
                e.close()
        except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
            return -1, {"detail": str(getattr(e, "reason", e))}

    def probe(self):
        """Find the API path (api/controller/v2/ first) and check the login: '' or why not."""
        if not self.conn["host"] or not self.conn["login"]:
            return NO_CREDENTIAL
        if self.ca_error:
            return self.ca_error
        for base in ("api/controller/v2/", "api/v2/"):
            self.base = base
            code, data = self.call("GET", "me/")
            if code != 404:
                break
        if code == 200:
            return ""
        if code < 0:
            return ("AAP did not answer at %s: %s (the address, a firewall, or its certificate - vm_deploy_aap_ca_path)"
                    % (self.conn["host"], error_text(data)))
        if code in (401, 403):
            return "AAP refused the login (HTTP %d): the credential's user name and password, or its token, are wrong or expired" % code
        if code == 404:
            return "AAP has no API at %s/api/controller/v2/ or /api/v2/ (HTTP 404): the credential's URL must be AAP's own address" % self.conn["host"]
        return "AAP answered HTTP %d: %s" % (code, error_text(data))


class Inventories(object):
    """The inventories and groups named, each read once."""

    def __init__(self, api):
        self.api, self.inv, self.grp = api, {}, {}

    def inventory(self, name):
        """(the inventory's record or None, why not)."""
        key = str(name).strip()
        if key not in self.inv:
            if key.isdigit():
                code, d = self.api.call("GET", "inventories/%s/" % key)
                found = [d] if code == 200 else []
            else:
                code, d = self.api.call("GET", "inventories/?name=%s" % quote(key))
                found = d.get("results", []) if code == 200 else []
            if code not in (200, 404):
                self.inv[key] = (None, "inventory %s could not be read (HTTP %d): %s" % (key, code, error_text(d)))
            elif not found:
                self.inv[key] = (None, "no inventory %s that the AAP user can see: create it in AAP (Automation Execution > "
                                       "Infrastructure > Inventories > Create inventory), or give the user the Admin role on it" % key)
            elif len(found) > 1:
                orgs = ", ".join(sorted(str((i.get("summary_fields") or {}).get("organization", {}).get("name", "?")) for i in found))
                self.inv[key] = (None, "%d inventories are named %s (organizations %s): name it by its ID, the number in its "
                                       "address in AAP (e.g. inventory: %s)" % (len(found), key, orgs, found[0].get("id")))
            else:
                i = found[0]
                kind = str(i.get("kind") or "")
                caps = (i.get("summary_fields") or {}).get("user_capabilities") or {}
                if kind in ("smart", "constructed"):
                    self.inv[key] = (None, "inventory %s is a %s inventory: AAP fills it from other inventories, it takes no hosts of "
                                           "its own - name one of the inventories it is made from" % (key, kind))
                elif caps.get("edit") is False:
                    self.inv[key] = (None, "the AAP user may not change inventory %s: give it the Admin role on that inventory" % key)
                else:
                    self.inv[key] = (i, "")
        return self.inv[key]

    def group(self, inv, name):
        key = (inv["id"], str(name).strip())
        if key not in self.grp:
            code, d = self.api.call("GET", "inventories/%s/groups/?name=%s" % (inv["id"], quote(key[1])))
            found = d.get("results", []) if code == 200 else []
            if code != 200:
                self.grp[key] = (None, "the groups of inventory %s could not be read (HTTP %d): %s" % (inv["name"], code, error_text(d)))
            elif not found:
                self.grp[key] = (None, "inventory %s has no group %s: create it in AAP (the inventory > Groups > Create group), or fix "
                                       "the group in vm_deploy_services" % (inv["name"], key[1]))
            else:
                self.grp[key] = (found[0], "")
        return self.grp[key]

    def overwrite_notes(self, inv, group):
        code, d = self.api.call("GET", "inventories/%s/inventory_sources/" % inv["id"])
        srcs = [s.get("name", "?") for s in d.get("results", []) if s.get("overwrite")] if code == 200 else []
        if not srcs:
            return []
        return ["inventory %s is filled by %s with Overwrite on: at its next sync AAP may take a host the deploy adds out of "
                "the groups that source fills%s - turn Overwrite off on that source, or add the server to the source's file too"
                % (inv["name"], ("source " + srcs[0]) if len(srcs) == 1 else ("sources " + ", ".join(srcs)),
                   (" (group %s, when it comes from the source)" % group) if group else "")]


def check(api, targets):
    invs, problems, notes = Inventories(api), [], []
    for t in targets:
        inv, why = invs.inventory(t.get("inventory") or "")
        if why:
            problems.append(why)
            continue
        if t.get("group"):
            _, why = invs.group(inv, t["group"])
            if why:
                problems.append(why)
                continue
        notes.extend(n for n in invs.overwrite_notes(inv, t.get("group") or "") if n not in notes)
    return problems, notes


def add(api, hosts, check_mode):
    invs, out = Inventories(api), []
    for h in hosts:
        r = {"name": h.get("name", ""), "vm": h.get("vm") or h.get("name", ""), "inventory": h.get("inventory", ""), "group": h.get("group") or "",
             "host": "", "in_group": "", "ok": False, "error": ""}
        out.append(r)
        try:
            add_one(api, invs, h, r, check_mode)
        except Exception as e:                  # noqa: BLE001 - one host's surprise never stops the others, nor the deploy
            r["ok"], r["error"] = False, "unexpected answer from AAP: %s" % e
    return out


def add_one(api, invs, h, r, check_mode):
    """One host into its inventory, then its group; fills r (its result) as it goes."""
    inv, why = invs.inventory(r["inventory"])
    grp, gwhy = (invs.group(inv, r["group"]) if inv and r["group"] else (None, ""))
    if why or gwhy:
        r["error"] = why or gwhy
        return
    code, d = api.call("GET", "inventories/%s/hosts/?name=%s" % (inv["id"], quote(r["name"])))
    if code != 200:
        r["error"] = "the hosts of inventory %s could not be read (HTTP %d): %s" % (inv["name"], code, error_text(d))
        return
    found = d.get("results") or []
    hid = found[0].get("id") if found else None
    if hid:
        r["host"] = "already there"
    elif check_mode:
        r["host"] = "would be added"
    else:
        body = {"name": r["name"], "description": h.get("description") or "", "enabled": True,
                "variables": json.dumps(h.get("variables") or {})}
        code, d = api.call("POST", "inventories/%s/hosts/" % inv["id"], body)
        if code not in (200, 201) or not d.get("id"):
            r["error"] = "AAP refused to add it to inventory %s (HTTP %d): %s" % (inv["name"], code, error_text(d))
            return
        hid, r["host"] = d["id"], "added"
    if grp:
        member = False
        if hid:
            code, d = api.call("GET", "groups/%s/hosts/?name=%s" % (grp["id"], quote(r["name"])))
            if code != 200:
                r["error"] = "group %s could not be read (HTTP %d): %s" % (r["group"], code, error_text(d))
                return
            member = bool(d.get("results"))
        if member:
            r["in_group"] = "already in it"
        elif check_mode:
            r["in_group"] = "would join"
        else:
            code, d = api.call("POST", "groups/%s/hosts/" % grp["id"], {"id": hid})
            if code not in (200, 201, 204):
                r["error"] = "AAP added it to inventory %s, but refused group %s (HTTP %d): %s" % (inv["name"], r["group"], code, error_text(d))
                return
            r["in_group"] = "joined"
    r["ok"] = True

def main():
    module = AnsibleModule(argument_spec=dict(action=dict(type="str", choices=["check", "add"], default="check"),
                                              targets=dict(type="list", elements="dict", default=[]),
                                              hosts=dict(type="list", elements="dict", default=[]),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"])
    why = api.probe()
    if why:
        if p["action"] == "add":
            module.exit_json(changed=False, api=api.base, problems=[why], notes=[],
                             hosts=[dict(name=h.get("name", ""), vm=h.get("vm") or h.get("name", ""), inventory=h.get("inventory", ""),
                                         group=h.get("group") or "", host="", in_group="", ok=False, error=why) for h in p["hosts"]])
        module.exit_json(changed=False, api=api.base, problems=[why], notes=[], hosts=[])
    if p["action"] == "check":
        problems, notes = check(api, p["targets"])
        module.exit_json(changed=False, api=api.base, problems=problems, notes=notes, hosts=[])
    hosts = add(api, p["hosts"], module.check_mode)
    module.exit_json(changed=any(h["host"] == "added" or h["in_group"] == "joined" for h in hosts), api=api.base,
                     problems=[], notes=[], hosts=hosts)


if __name__ == "__main__":
    main()
