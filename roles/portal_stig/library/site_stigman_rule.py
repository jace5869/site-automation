#!/usr/bin/python
# -*- coding: utf-8 -*-
"""One STIG rule's text from STIG Manager (read-only), for "STIG - fix one rule" (1.67.0, workflow 47): its title,
severity, DISA's check text and fix text. A token from the Keycloak client (client credentials), then
GET /stigs/{benchmarkId}/revisions/{revision}/rules/{ruleId} with the projections checks, fixes and detail. Runs on the
AAP side (delegated to localhost). Never writes. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stigman_rule
short_description: One STIG rule's title, check text and fix text from STIG Manager (read-only)
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  benchmark: {description: The STIG (RHEL_9_STIG)., type: str, required: true}
  revision: {description: "Its revision (V2R8), or latest.", type: str, default: latest}
  rule: {description: "The rule (SV-257985r925318_rule).", type: str, required: true}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: A CA bundle., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
author: site automation
'''

RETURN = r'''
rule: {description: "{id, version, title, severity, check, fix, discussion}", type: dict}
error: {description: Why the rule could not be read ('' when it was)., type: str}
'''

import json
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import quote, urlencode
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

TEXT_MAX = 6000


class Client(object):
    """As site_stigman_read's: a client-credentials token, then GETs."""
    def __init__(self, p):
        self.p = p
        self.api = p["api"].rstrip("/")
        self.ctx = ssl._create_unverified_context() if not p["validate_certs"] else ssl.create_default_context(cafile=p["ca_path"] or None)
        self.token = None

    def _open(self, req):
        try:
            resp = urlopen(req, timeout=self.p["timeout"], context=self.ctx)
            return json.loads(resp.read().decode("utf-8") or "null")
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("HTTP %d from %s: %s" % (e.code, req.get_full_url().split("?")[0], body.strip() or e.reason))
        except URLError as e:
            raise RuntimeError("cannot reach %s: %s" % (req.get_full_url().split("?")[0], e.reason))
        except ValueError:
            raise RuntimeError("%s did not answer JSON" % req.get_full_url().split("?")[0])

    def login(self):
        form = {"grant_type": "client_credentials", "client_id": self.p["client_id"], "client_secret": self.p["client_secret"]}
        if self.p["scope"]:
            form["scope"] = self.p["scope"]
        req = Request(self.p["token_url"], data=urlencode(form).encode("utf-8"),
                      headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        ans = self._open(req) or {}
        if not ans.get("access_token"):
            raise RuntimeError("the token endpoint gave no access_token")
        self.token = ans["access_token"]

    def get(self, path, **query):
        q = urlencode(sorted(query.items()), doseq=True)
        req = Request(self.api + path + ("?" + q if q else ""),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return self._open(req)


def _text(items, key):
    """The texts of a rule's checks (content) or fixes (text), joined, capped."""
    out = [str((x or {}).get(key) or "").strip() for x in items or [] if isinstance(x, dict)]
    t = "\n\n".join(x for x in out if x)
    return t[:TEXT_MAX] + ("\n[... cut]" if len(t) > TEXT_MAX else "")


def rule_of(ans, rule_id):
    """STIG Manager's answer -> {id, version, title, severity, check, fix, discussion}."""
    a = ans if isinstance(ans, dict) else {}
    detail = a.get("detail") if isinstance(a.get("detail"), dict) else {}
    checks = a.get("checks") if isinstance(a.get("checks"), list) else ([a["check"]] if isinstance(a.get("check"), dict) else [])
    fixes = a.get("fixes") if isinstance(a.get("fixes"), list) else ([a["fix"]] if isinstance(a.get("fix"), dict) else [])
    return {"id": str(a.get("ruleId") or rule_id), "version": str(a.get("version") or ""), "title": str(a.get("title") or ""),
            "severity": str(a.get("severity") or ""), "check": _text(checks, "content"), "fix": _text(fixes, "text"),
            "discussion": str(detail.get("vulnDiscussion") or "")[:TEXT_MAX]}


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True), token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True), client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""), benchmark=dict(type="str", required=True),
        revision=dict(type="str", default="latest"), rule=dict(type="str", required=True),
        validate_certs=dict(type="bool", default=True), ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60)), supports_check_mode=True)
    p = module.params
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        module.exit_json(changed=False, rule={}, error="no token for the client %s: %s" % (p["client_id"], e))
    path = "/stigs/%s/revisions/%s/rules/%s" % (quote(p["benchmark"], safe=""), quote(p["revision"] or "latest", safe=""),
                                                quote(p["rule"], safe=""))
    try:
        ans = client.get(path, projection=["detail", "checks", "fixes"])
    except RuntimeError as e:
        module.exit_json(changed=False, rule={}, error="the rule %s of %s could not be read: %s" % (p["rule"], p["benchmark"], e))
    r = rule_of(ans, p["rule"])
    module.exit_json(changed=False, rule=r,
                     error="" if r["fix"] else "STIG Manager has no fix text for %s (%s)" % (p["rule"], p["benchmark"]))


if __name__ == "__main__":
    main()
