#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The vCenter appliance's own access settings - its VAMI's Access page (https://vcenter:5480): SSH Login, DCUI,
Console CLI and Bash Shell - set through vCenter's REST API with the job's vCenter credential. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vcsa_access
short_description: Set the vCenter appliance's SSH Login, DCUI, Console CLI and Bash Shell (its VAMI's Access page)
description:
  - For every vCenter of the job (VMWARE_HOST and vcenters): reads the appliance's access settings
    (GET /api/appliance/access/ssh, dcui, consolecli, shell; /rest on 6.7) and sets the ones asked for that differ
    (PUT). Each is read back after it is set. Check mode reads only and says what it would change.
  - The account (VMWARE_USER, VMWARE_PASSWORD - the "VMware vCenter" credential) must be allowed to change the
    appliance's settings - in vCenter's single sign-on group SystemConfiguration.Administrators, as
    administrator@vsphere.local is; otherwise vCenter answers 403, said with that.
  - Bash Shell turned on turns itself off after shell_timeout seconds (as the VAMI's own timeout); one already on is
    left to its own countdown.
options:
  vcenters: {description: More vCenters besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  ssh: {description: SSH Login. Empty = leave it., type: str, choices: [enabled, disabled, ''], default: ''}
  dcui: {description: DCUI (the appliance's console screen). Empty = leave it., type: str, choices: [enabled, disabled, ''], default: ''}
  consolecli: {description: Console CLI (the appliance shell on its console). Empty = leave it., type: str, choices: [enabled, disabled, ''], default: ''}
  shell: {description: Bash Shell. Empty = leave it., type: str, choices: [enabled, disabled, ''], default: ''}
  shell_timeout: {description: "Seconds Bash Shell stays on when this turns it on (1 to 86400).", type: int, default: 3600}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds for one request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
appliances:
  description: One record per vCenter.
  type: list
  returned: success
  sample: [{name: vc01.example.mil, status: changed, before: {SSH Login: Activated, DCUI: Deactivated, Console CLI: Activated,
            Bash Shell: Deactivated}, changes: ["SSH Login: Activated -> Deactivated"], errors: []}]
'''

import base64
import json
import os
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_vcenters import NO_VCENTER, host_port, vcenter_list
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

# option -> (the API's name, the VAMI's)
ACCESS = (("ssh", "ssh", "SSH Login"), ("dcui", "dcui", "DCUI"), ("consolecli", "consolecli", "Console CLI"), ("shell", "shell", "Bash Shell"))
NO_RIGHTS = ("the account may not change the appliance's settings: put it in vCenter's single sign-on group "
             "SystemConfiguration.Administrators (docs/VMWARE.md, ESXi security settings)")


def word(on):
    return "Activated" if on else "Deactivated"


class Api(object):
    """vCenter's REST API with the credential's account: /api (vCenter 7.0 U2 and later), else /rest (6.7, 7.0)."""

    def __init__(self, host, port, validate_certs, timeout):
        self.base = "https://%s:%d" % ("[%s]" % host if ":" in host else host, port)
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        if not validate_certs:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.token, self.old = "", False

    def call(self, method, path, auth=False, body=None):
        headers = {"Accept": "application/json"}
        if auth:
            pair = "%s:%s" % (os.environ.get("VMWARE_USER", ""), os.environ.get("VMWARE_PASSWORD", ""))
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
        elif self.token:
            headers["vmware-api-session-id"] = self.token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urlrequest.Request(self.base + path, data=data, headers=headers)
        req.get_method = lambda: method
        try:
            resp = urlrequest.urlopen(req, timeout=self.timeout, context=self.ctx)
            try:
                code, raw = resp.getcode(), resp.read()
            finally:
                resp.close()
        except urlerror.HTTPError as e:
            try:
                code, raw = e.code, e.read()
            finally:
                e.close()
        try:
            return code, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]

    def login(self, name):
        code, d = self.call("POST", "/api/session", auth=True)
        if code == 404:
            self.old = True
            code, d = self.call("POST", "/rest/com/vmware/cis/session", auth=True)
            d = d.get("value") if isinstance(d, dict) else d
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused the login to its API (HTTP %d)" % (name, code))
        if code not in (200, 201) or not isinstance(d, str) or not d:
            raise RuntimeError("vCenter %s: no API session (HTTP %s)" % (name, code))
        self.token = d

    def logout(self):
        if self.token:
            try:
                self.call("DELETE", "/rest/com/vmware/cis/session" if self.old else "/api/session")
            except Exception:                   # noqa: BLE001
                pass
            self.token = ""

    def path(self, what):
        return ("/rest" if self.old else "/api") + "/appliance/access/" + what

    def get(self, what):
        code, d = self.call("GET", self.path(what))
        if self.old and isinstance(d, dict) and "value" in d:
            d = d["value"]
        if code == 403:
            raise RuntimeError(NO_RIGHTS + " (HTTP 403 reading %s)" % what)
        if code != 200:
            raise RuntimeError("reading %s: HTTP %s %s" % (what, code, why(d)))
        return d

    def put(self, what, spec):
        body = ({"config": spec} if what == "shell" else spec) if self.old else spec
        code, d = self.call("PUT", self.path(what), body=body)
        if code == 403:
            raise RuntimeError(NO_RIGHTS + " (HTTP 403 setting %s)" % what)
        if code not in (200, 204):
            raise RuntimeError("setting %s: HTTP %s %s" % (what, code, why(d)))


def why(d):
    """vCenter's error body -> its message ('' = none)."""
    if isinstance(d, dict):
        v = d.get("value") if isinstance(d.get("value"), dict) else d
        msgs = v.get("messages") or []
        return "- " + "; ".join(m.get("default_message", "") for m in msgs if isinstance(m, dict)) if msgs else ""
    return ""


def state(d):
    """The API's answer -> (on, its words): a bool, or the shell's {enabled, timeout}."""
    if isinstance(d, dict):
        on = bool(d.get("enabled"))
        return on, word(on) + (" (turns off in %s s)" % d["timeout"] if on and d.get("timeout") else "")
    return bool(d), word(bool(d))


def appliance(api, name, want, shell_timeout, check):
    """One vCenter's appliance: read each setting, set the ones that differ, read them back."""
    rec = {"name": name, "status": "ok", "before": {}, "changes": [], "errors": []}
    api.login(name)
    try:
        for opt, what, label in ACCESS:
            try:
                on, words = state(api.get(what))
            except RuntimeError as e:
                rec["errors"].append("%s: %s" % (label, e))
                continue
            rec["before"][label] = words
            if not want.get(opt) or on == (want[opt] == "enabled"):
                continue
            to = want[opt] == "enabled"
            change = "%s: %s -> %s" % (label, word(on), word(to)) + (" (turns off in %d s)" % shell_timeout if to and what == "shell" else "")
            if check:
                rec["changes"].append(change)
                continue
            try:
                api.put(what, {"enabled": to, "timeout": shell_timeout if to else 0} if what == "shell" else {"enabled": to})
                now, _ = state(api.get(what))
                if now != to:
                    raise RuntimeError("set, but it reads %s after" % word(now))
                rec["changes"].append(change)
            except RuntimeError as e:
                rec["errors"].append("%s: %s" % (label, e))
    finally:
        api.logout()
    rec["status"] = "failed" if rec["errors"] else ("changed" if rec["changes"] else "ok")
    return rec


def main():
    module = AnsibleModule(argument_spec=dict(
        vcenters=dict(type="list", elements="str", default=[]),
        ssh=dict(type="str", choices=["enabled", "disabled", ""], default=""),
        dcui=dict(type="str", choices=["enabled", "disabled", ""], default=""),
        consolecli=dict(type="str", choices=["enabled", "disabled", ""], default=""),
        shell=dict(type="str", choices=["enabled", "disabled", ""], default=""),
        shell_timeout=dict(type="int", default=3600),
        validate_certs=dict(type="bool", default=True),
        timeout=dict(type="int", default=30),
    ), supports_check_mode=True)
    p = module.params
    if not 1 <= p["shell_timeout"] <= 86400:
        module.fail_json(msg="shell_timeout is %d: 1 to 86400 seconds" % p["shell_timeout"])
    want = dict((opt, p[opt]) for opt, _w, _l in ACCESS)
    names = vcenter_list(p["vcenters"])
    if not names:
        module.fail_json(msg=NO_VCENTER)
    out, seen = [], set()
    for name in names:
        host, port = host_port(name)
        if (host.lower(), port) in seen:
            continue
        seen.add((host.lower(), port))
        try:
            out.append(appliance(Api(host, port, p["validate_certs"], p["timeout"]), name, want, p["shell_timeout"], module.check_mode))
        except Exception as e:                  # noqa: BLE001 - one vCenter's error never stops the others
            out.append({"name": name, "status": "failed", "before": {}, "changes": [], "errors": [str(e)]})
    module.exit_json(changed=any(a["status"] == "changed" for a in out) and not module.check_mode, appliances=out)


if __name__ == "__main__":
    main()
