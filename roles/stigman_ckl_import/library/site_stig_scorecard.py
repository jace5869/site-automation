#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The STIG compliance scorecard's read of STIG Manager (1.58.0; playbooks/stigman_ckl_import.yml with
stigman_ckl_import_mode "import and scorecard" or "scorecard"): each collection the client can read, how many assets it
has, and its open findings - a row per rule with the assets it is open on. Read-only: the token's POST to Keycloak, then
GET requests only. Never writes to STIG Manager, never reads the share. Runs on the AAP side. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_scorecard
short_description: The open findings of each STIG Manager collection, read-only
description:
  - Gets a token from C(token_url) with the client credentials grant and lists the collections the client can read
    (GET /collections) - all of them, or only those named in C(collections) (a name or an id, any case).
  - Per collection reads the open findings by rule with their assets
    (GET /collections/ID/findings?aggregator=ruleId&acceptedOnly=false&projection=assets) and the number of assets
    (GET /collections/ID/metrics/summary/collection; an older STIG Manager without it - GET /assets?collectionId=ID).
  - The counts are taken from the findings themselves - CAT I open is the number of (rule, asset) pairs of severity high - so
    the tiles, the change since last week and the rule lists always agree.
  - A collection that cannot be read is in the result with its error; the others are read. Only GET requests to STIG
    Manager. The client needs the scope stig-manager:collection:read and a grant in the collections.
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client (e.g. autostig)., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  collections: {description: "Only these collections, by name or id ([] = every collection the client can read).", type: list, elements: str, default: []}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
author: site automation
'''

RETURN = r'''
ok: {description: The token and the list of collections were read., type: bool}
error: {description: "Why nothing could be read, in plain words ('' = read).", type: str}
read_at: {description: "When this was read (UTC, 2026-11-13T06:00:05Z).", type: str}
collections:
  description: "Per collection, by name: {id, name, assets, cat1, cat2, cat3 (open findings: rule and asset pairs), rules: [{rule
                (its id), sv (SV-257985), cat (1, 2 or 3), version (the STIG ID), title, assets: [names]}], error ('' = read)}"
  type: list
missing: {description: "The names of C(collections) that the client cannot read or that are not there.", type: list}
'''

import json
import re
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

SV_RE = re.compile(r"(SV-\d+)")
CAT = {"high": 1, "medium": 2, "low": 3}


def sv_of(rule_id):
    """SV-257985r1069364_rule -> SV-257985 (a rule keeps its number across the STIG's revisions); another id as it is."""
    m = SV_RE.search(str(rule_id or ""))
    return m.group(1) if m else str(rule_id or "")


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


def wanted(cols, names):
    """(the collections to read, the names not found): names are collection names or ids, any case; [] or ['*'] = all."""
    names = [str(n).strip() for n in names or [] if str(n).strip()]
    if not names or "*" in names:
        return list(cols), []
    out, missing = [], []
    for n in names:
        hit = [c for c in cols if str(c.get("name", "")).lower() == n.lower() or str(c.get("collectionId")) == n]
        if not hit:
            missing.append(n)
        for c in hit:
            if c not in out:
                out.append(c)
    return out, missing


def findings_of(rows):
    """STIG Manager's findings by rule -> (rules, {1: n, 2: n, 3: n}): a rule's assets by name, each once; a finding
    without the assets projection counts its assetCount."""
    rules, cats = [], {1: 0, 2: 0, 3: 0}
    for f in rows or []:
        if not isinstance(f, dict) or not f.get("ruleId"):
            continue
        cat = CAT.get(str(f.get("severity", "")).lower(), 2)
        names = sorted(set(str(a.get("name") or a.get("assetId") or "") for a in f.get("assets") or [] if isinstance(a, dict)) - {""},
                       key=lambda s: s.lower())
        n = len(names) if names else int(f.get("assetCount") or 0)
        if n <= 0:
            continue
        cats[cat] += n
        rules.append({"rule": str(f["ruleId"]), "sv": sv_of(f["ruleId"]), "cat": cat, "version": str(f.get("version") or ""),
                      "title": " ".join(str(f.get("title") or "").split()), "assets": names, "count": n})
    rules.sort(key=lambda r: (r["cat"], -r["count"], r["sv"]))
    return rules, cats


def asset_count(client, cid):
    try:
        m = client.get("/collections/%s/metrics/summary/collection" % cid)
        if isinstance(m, dict) and isinstance(m.get("assets"), int):
            return m["assets"]
    except RuntimeError:
        pass                                    # an older STIG Manager, or no grant for the metrics: count the assets
    rows = client.get("/assets", collectionId=cid)
    return len(rows or [])


def read(p, client):
    cols = client.get("/collections") or []
    mine, missing = wanted([c for c in cols if isinstance(c, dict) and c.get("collectionId") is not None], p["collections"])
    out = []
    for c in sorted(mine, key=lambda c: str(c.get("name", "")).lower()):
        cid = str(c["collectionId"])
        rec = {"id": cid, "name": str(c.get("name") or cid), "assets": 0, "cat1": 0, "cat2": 0, "cat3": 0, "rules": [], "error": ""}
        try:
            rows = client.get("/collections/%s/findings" % cid, aggregator="ruleId", acceptedOnly="false", projection="assets")
            rec["rules"], cats = findings_of(rows)
            rec.update(cat1=cats[1], cat2=cats[2], cat3=cats[3])
            rec["assets"] = asset_count(client, cid)
        except RuntimeError as e:
            rec["error"] = str(e)
        out.append(rec)
    return out, missing


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        collections=dict(type="list", elements="str", default=[]),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
    ), supports_check_mode=True)
    p = module.params
    res = {"changed": False, "ok": False, "error": "", "collections": [], "missing": [],
           "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if not (p["api"] and p["token_url"] and p["client_id"] and p["client_secret"]):
        res["error"] = "no STIG Manager or no login for it (check_stig_stigman_api, the \"STIG Manager API\" credential)"
        module.exit_json(**res)
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        res["error"] = "no token for the client %s: %s" % (p["client_id"], e)
        module.exit_json(**res)
    try:
        res["collections"], res["missing"] = read(p, client)
    except RuntimeError as e:
        res["error"] = "STIG Manager could not be read: %s" % e
        module.exit_json(**res)
    res["ok"] = True
    module.exit_json(**res)


if __name__ == "__main__":
    main()
