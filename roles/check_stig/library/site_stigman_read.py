#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read each host's STIG reviews from STIG Manager (read-only): a token from the Keycloak client (client
credentials), the collections the client can read, each host's asset in its collection, and the asset's reviews
of the host's STIG - one record per STIG rule. Runs on the AAP side (delegated to localhost). Never writes to
STIG Manager. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stigman_read
short_description: Each host's STIG reviews from STIG Manager, read-only
description:
  - Gets a token from C(token_url) with the client credentials grant, lists the collections the client can read,
    finds each host's asset (by name or FQDN, in the host's collection or in any collection the client can read)
    and reads the asset's reviews of the host's STIG (C(benchmark)).
  - Only GET requests to STIG Manager. The client needs the scope stig-manager:collection:read and a grant in
    the collections.
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client (e.g. autostig)., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  hosts:
    description: "[{host, names: [asset names to try], collection: '' or a name, benchmark: RHEL_9_STIG}]"
    type: list
    elements: dict
    required: true
  collections: {description: "Only these collections are searched ([] = every collection the client can read).", type: list, elements: str, default: []}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
author: site automation
'''

RETURN = r'''
hosts:
  description: "Per host: {status: found / not_found / several / no_benchmark / error, why, collection, asset, assetId,
                revision, reviews: {SV-257985: {result, status, ts, rule, version, severity, title}}}"
  type: dict
  returned: always
error:
  description: Why nothing could be read (the token, the collections), in plain words ('' = read).
  type: str
  returned: always
'''

import json
import re
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode, quote
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

SV_RE = re.compile(r"(SV-\d+)")


def sv_of(rule_id):
    """SV-257985r1069364_rule -> SV-257985 (the STIG rule, whatever its revision)."""
    m = SV_RE.match(rule_id or "")
    return m.group(1) if m else ""


def short(name):
    return (name or "").split(".")[0].lower()


def match_assets(names, assets):
    """The assets whose name or FQDN is one of the host's names: an exact (case-blind) name or FQDN first, then the
    short names (web01 = web01.example.mil)."""
    want = [n.lower() for n in names if n]
    exact = [a for a in assets if (a.get("name") or "").lower() in want or (a.get("fqdn") or "").lower() in want]
    if exact:
        return exact
    shorts = set(short(n) for n in want)
    return [a for a in assets if short(a.get("name")) in shorts or (a.get("fqdn") and short(a.get("fqdn")) in shorts)]


def reviews_of(rows):
    """STIG Manager's reviews of one asset -> {SV id: record}; a review that covers several revisions of a rule
    (ruleIds) counts for each of them."""
    out = {}
    for r in rows or []:
        rule = r.get("rule") or {}
        st = r.get("status") or {}
        rec = {"result": r.get("result") or "", "status": st.get("label", "") if isinstance(st, dict) else str(st),
               "ts": r.get("ts") or r.get("touchTs") or "", "rule": r.get("ruleId") or "",
               "version": rule.get("version") or "", "severity": rule.get("severity") or "", "title": rule.get("title") or ""}
        for rid in [r.get("ruleId")] + list(r.get("ruleIds") or []):
            k = sv_of(rid)
            if k and k not in out:
                out[k] = rec
    return out


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
        q = urlencode(sorted(query.items()))
        req = Request(self.api + path + ("?" + q if q else ""),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return self._open(req)


def read(p, client):
    cols = client.get("/collections") or []
    if p["collections"]:
        want = [c.lower() for c in p["collections"]]
        cols = [c for c in cols if (c.get("name") or "").lower() in want]
    by_name = dict(((c.get("name") or "").lower(), c) for c in cols)
    assets_cache, out = {}, {}

    def assets(col, bench):
        key = (col["collectionId"], bench)
        if key not in assets_cache:
            assets_cache[key] = [dict(a, _col=col) for a in
                                 client.get("/assets", collectionId=col["collectionId"], benchmarkId=bench, projection="stigs") or []]
        return assets_cache[key]

    for h in p["hosts"]:
        host, bench = h.get("host", ""), h.get("benchmark") or ""
        names = [n for n in (h.get("names") or [host]) if n]
        if not bench:
            out[host] = {"status": "no_benchmark", "why": "no STIG Manager benchmark for this host's OS (check_stig_stigman_benchmarks)"}
            continue
        if h.get("collection"):
            col = by_name.get(h["collection"].lower())
            if not col:
                out[host] = {"status": "error", "why": "the collection %s is not there, or the client has no grant in it" % h["collection"]}
                continue
            search = [col]
        else:
            search = cols
        try:
            cands = []
            for col in search:
                cands += assets(col, bench)
            found = match_assets(names, cands)
        except RuntimeError as e:
            out[host] = {"status": "error", "why": str(e)}
            continue
        if not found:
            out[host] = {"status": "not_found", "why": "no asset named %s with %s in %s" % (
                " or ".join(names), bench, ("the collection " + search[0]["name"]) if h.get("collection") else
                "any collection the client can read (%d)" % len(search))}
            continue
        if len(found) > 1:
            out[host] = {"status": "several", "why": "%d assets match: %s - set check_stig_stigman_collection (or the asset name) for this host" % (
                len(found), ", ".join("%s in %s" % (a.get("name"), a["_col"].get("name")) for a in found))}
            continue
        a = found[0]
        rev = ""
        for s in a.get("stigs") or []:
            if s.get("benchmarkId") == bench:
                rev = s.get("revisionStr") or ""
        try:
            rows = client.get("/collections/%s/reviews/%s" % (quote(str(a["_col"]["collectionId"])), quote(str(a["assetId"]))),
                              benchmarkId=bench, projection="rule")
        except RuntimeError as e:
            out[host] = {"status": "error", "why": str(e), "collection": a["_col"].get("name"), "asset": a.get("name")}
            continue
        out[host] = {"status": "found", "why": "", "collection": a["_col"].get("name", ""), "asset": a.get("name", ""),
                     "assetId": str(a.get("assetId")), "benchmark": bench, "revision": rev, "reviews": reviews_of(rows)}
    return out


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        hosts=dict(type="list", elements="dict", required=True),
        collections=dict(type="list", elements="str", default=[]),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
    ), supports_check_mode=True)
    p = module.params
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        module.exit_json(changed=False, hosts={}, error="no token for the client %s: %s" % (p["client_id"], e))
    try:
        hosts = read(p, client)
    except RuntimeError as e:
        module.exit_json(changed=False, hosts={}, error="STIG Manager could not be read: %s" % e)
    module.exit_json(changed=False, hosts=hosts, error="")


if __name__ == "__main__":
    main()
