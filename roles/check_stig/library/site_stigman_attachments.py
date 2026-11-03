#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Which reviews in STIG Manager have attachments (read-only). STIG Manager keeps a review's attachments in its metadata:
the key "artifacts" lists them (name, digest, type, size), each file under a key of its own (its digest). Workflow 37's
check (playbooks/stig_recheck.yml) leaves those reviews out of what it hands to the approval: the evidence a person
attached stays with the result they gave. GET requests only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stigman_attachments
short_description: Which STIG Manager reviews have attachments (read-only)
description:
  - For each item (collectionId, assetId, ruleId) reads the review's metadata keys
    (GET /collections/{c}/reviews/{asset}/{rule}/metadata/keys) and, when it has the key C(artifacts), that key's value -
    the list of attachments STIG Manager's review panel shows.
  - A review that cannot be read is said (error), and counts as having attachments - left out rather than written blind.
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client (read access is enough)., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  items: {description: "[{collectionId, assetId, ruleId}] - e.g. stig_update_plan's items.", type: list, elements: dict, required: true}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
author: site automation
'''

RETURN = r'''
reviews:
  description: "One per item, in order: {collectionId, assetId, ruleId, attachments (how many; -1 = some, not readable),
    names (the files), error}."
  type: list
  returned: always
error:
  description: Why nothing could be read (the token), in plain words ('' = read).
  type: str
  returned: always
'''

import json
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode, quote
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError


class NotFound(Exception):
    pass


class Client(object):
    def __init__(self, p):
        self.p = p
        self.api = p["api"].rstrip("/")
        if not p["validate_certs"]:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(cafile=p["ca_path"] or None)
        self.token = None

    def _open(self, req):
        try:
            resp = urlopen(req, timeout=self.p["timeout"], context=self.ctx)
            return json.loads(resp.read().decode("utf-8") or "null")
        except HTTPError as e:
            if e.code == 404:
                raise NotFound()
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

    def get(self, path):
        return self._open(Request(self.api + path, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"}))


def artifacts_of(value):
    """The value of the key 'artifacts' (a JSON text of a list, as STIG Manager's review panel writes it) -> (how many,
    their names); (-1, []) when it is there but not such a list."""
    v = value
    for _ in range(2):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                return (-1, []) if v.strip() not in ("", "[]") else (0, [])
    if isinstance(v, list):
        return len(v), [str((a or {}).get("name") or "") for a in v if isinstance(a, dict)]
    return (0, []) if v in (None, "", {}) else (-1, [])


def attachments(client, c, a, rule):
    """(how many attachments the review has, their names): 0 for a review without the key, or no review at all."""
    base = "/collections/%s/reviews/%s/%s/metadata/keys" % (quote(str(c)), quote(str(a)), quote(str(rule)))
    try:
        keys = client.get(base) or []
    except NotFound:
        return 0, []
    if "artifacts" not in keys:
        return 0, []
    try:
        return artifacts_of(client.get(base + "/artifacts"))
    except NotFound:
        return 0, []


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        items=dict(type="list", elements="dict", required=True),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
    ), supports_check_mode=True)
    p = module.params
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        module.exit_json(changed=False, reviews=[], error="no token for the client %s: %s" % (p["client_id"], e))
    out = []
    for it in p["items"]:
        row = {"collectionId": str(it.get("collectionId", "")), "assetId": str(it.get("assetId", "")),
               "ruleId": str(it.get("ruleId", "")), "attachments": 0, "names": [], "error": ""}
        try:
            row["attachments"], row["names"] = attachments(client, row["collectionId"], row["assetId"], row["ruleId"])
        except RuntimeError as e:
            row.update(attachments=-1, error=str(e))
        out.append(row)
    module.exit_json(changed=False, reviews=out, error="")


if __name__ == "__main__":
    main()
