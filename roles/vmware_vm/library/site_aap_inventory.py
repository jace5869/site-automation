#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Check AAP inventories and groups, and add hosts to them, through AAP's own API: the VMs the
deploy built join the inventory and group of their service (vm_deploy_services). The address and
login come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py).
Standard library only. Never creates an inventory or a group: a name that is not there is refused, never guessed."""
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

import json
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api as AapApi, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the inventory)')


class Api(AapApi):
    def __init__(self, conn, ca_path="", timeout=30):
        AapApi.__init__(self, conn, ca_path, timeout, ca_setting="vm_deploy_aap_ca_path", no_credential=NO_CREDENTIAL)


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
