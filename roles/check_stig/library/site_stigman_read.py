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
    description: "[{host, names: [asset names to try], collection: '' or a name, benchmark: RHEL_9_STIG, id: the key of its answer
      ('' = host; a host with several STIGs has one entry per STIG, each with its own id), rules: true - the STIG revision's
      rules too (the STIG recheck: the rules with no review), every: true - no benchmark: every STIG assigned to the host's
      asset and to its web site and database assets (<asset>-<site>-<instance>), each a checklist}]"
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
  description: "Per host (per id): {status: found / not_found / no_stig (the asset is there, without this STIG) / several /
                no_benchmark / error, why, collection, asset, assetId, revision, reviews: {SV-257985: {result, status, ts, rule,
                version, severity, title}}, rules (with rules: true): {SV-257985: {v, rule, version, severity, title}}};
                with every: true {status, why, collection, asset, checklists: [{asset, assetId, benchmark, revision, site,
                instance, reviews, rules}]}"
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


def rules_of(rows):
    """A STIG revision's rules -> {SV id: {v, rule, version, severity, title}}."""
    out = {}
    for r in rows or []:
        k = sv_of(r.get("ruleId"))
        if k and k not in out:
            out[k] = {"v": r.get("groupId") or ("V-" + k[3:]), "rule": r.get("ruleId") or "", "version": r.get("version") or "",
                      "severity": r.get("severity") or "", "title": r.get("title") or ""}
    return out


def tail_of(base, name):
    """A web site or database asset STIG Manager named <host>-<site>-<instance> when it imported its checklist (NA for none)
    -> (site, instance); None when the name is not one of the host's."""
    b, n = (base or "").lower(), (name or "")
    if not b or not n.lower().startswith(b + "-") or len(n) <= len(b) + 1:
        return None
    rest = n[len(b) + 1:]
    if "-" not in rest:
        return None
    site, inst = rest.rsplit("-", 1)
    return ("" if site == "NA" else site, "" if inst == "NA" else inst)


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
    assets_cache, rules_cache, out = {}, {}, {}

    def assets(col, bench):
        key = (col["collectionId"], bench)
        if key not in assets_cache:
            q = {"collectionId": col["collectionId"], "projection": "stigs"}
            if bench:
                q["benchmarkId"] = bench
            assets_cache[key] = [dict(a, _col=col) for a in client.get("/assets", **q) or []]
        return assets_cache[key]

    def rules(bench, rev):
        """The rules of a STIG revision (the asset's, else the latest STIG Manager has)."""
        key = (bench, rev or "latest")
        if key not in rules_cache:
            rules_cache[key] = rules_of(client.get("/stigs/%s/revisions/%s/rules" % (quote(bench), quote(rev or "latest"))))
        return rules_cache[key]

    def reviews(a, bench):
        return reviews_of(client.get("/collections/%s/reviews/%s" % (quote(str(a["_col"]["collectionId"])), quote(str(a["assetId"]))),
                                     benchmarkId=bench, projection="rule"))

    for h in p["hosts"]:
        host, bench = h.get("host", ""), h.get("benchmark") or ""
        hid = h.get("id") or host
        names = [n for n in (h.get("names") or [host]) if n]
        if h.get("every"):
            out[hid] = every(h, names, by_name, cols, assets, rules, reviews)
            continue
        if not bench:
            out[hid] = {"status": "no_benchmark", "why": "no STIG Manager benchmark for this host's OS (check_stig_stigman_benchmarks)"}
            continue
        if h.get("collection"):
            col = by_name.get(h["collection"].lower())
            if not col:
                out[hid] = {"status": "error", "why": "the collection %s is not there, or the client has no grant in it" % h["collection"]}
                continue
            search = [col]
        else:
            search = cols
        try:
            cands = []
            for col in search:
                cands += assets(col, bench)
            found = match_assets(names, cands)
            bare = []
            if not found:
                # the asset may be there without this STIG assigned: not the same as no asset
                for col in search:
                    bare += assets(col, "")
                bare = match_assets(names, bare)
        except RuntimeError as e:
            out[hid] = {"status": "error", "why": str(e)}
            continue
        if not found and bare:
            a = bare[0]
            out[hid] = {"status": "no_stig", "why": "the asset %s (%s) does not have %s assigned in STIG Manager" % (
                a.get("name"), a["_col"].get("name"), bench), "collection": a["_col"].get("name", ""), "asset": a.get("name", "")}
            continue
        if not found:
            out[hid] = {"status": "not_found", "why": "no asset named %s with %s in %s" % (
                " or ".join(names), bench, ("the collection " + search[0]["name"]) if h.get("collection") else
                "any collection the client can read (%d)" % len(search))}
            continue
        if len(found) > 1:
            out[hid] = {"status": "several", "why": "%d assets match: %s - set check_stig_stigman_collection (or the asset name) for this host" % (
                len(found), ", ".join("%s in %s" % (a.get("name"), a["_col"].get("name")) for a in found))}
            continue
        a = found[0]
        rev = ""
        for s in a.get("stigs") or []:
            if s.get("benchmarkId") == bench:
                rev = s.get("revisionStr") or ""
        try:
            rv = reviews(a, bench)
            rl = rules(bench, rev) if h.get("rules") else None
        except RuntimeError as e:
            out[hid] = {"status": "error", "why": str(e), "collection": a["_col"].get("name"), "asset": a.get("name")}
            continue
        out[hid] = {"status": "found", "why": "", "collection": a["_col"].get("name", ""), "asset": a.get("name", ""),
                     "assetId": str(a.get("assetId")), "benchmark": bench, "revision": rev, "reviews": rv}
        if rl is not None:
            out[hid]["rules"] = rl
    return out


def every(h, names, by_name, cols, assets, rules, reviews):
    """A host's every checklist in STIG Manager (the STIG recheck on Windows): its asset's STIGs, and the STIGs of its web
    site and database assets (<asset>-<site>-<instance>, in the same collection), each with its reviews and rules."""
    if h.get("collection"):
        col = by_name.get(h["collection"].lower())
        if not col:
            return {"status": "error", "why": "the collection %s is not there, or the client has no grant in it" % h["collection"]}
        search = [col]
    else:
        search = cols
    try:
        cands = []
        for col in search:
            cands += assets(col, "")
        found = match_assets(names, cands)
        if not found:
            return {"status": "not_found", "why": "no asset named %s in %s" % (
                " or ".join(names), ("the collection " + search[0]["name"]) if h.get("collection") else
                "any collection the client can read (%d)" % len(search))}
        if len(found) > 1:
            return {"status": "several", "why": "%d assets match: %s - set check_stig_stigman_collection (or the asset name) for this host" % (
                len(found), ", ".join("%s in %s" % (a.get("name"), a["_col"].get("name")) for a in found))}
        a = found[0]
        mine = [(a, "", "")]
        for b in assets(a["_col"], ""):
            t = tail_of(a.get("name"), b.get("name"))
            if t and b.get("assetId") != a.get("assetId"):
                mine.append((b, t[0], t[1]))
        lists = []
        for x, site, inst in mine:
            for s in x.get("stigs") or []:
                bench = s.get("benchmarkId") or ""
                if not bench:
                    continue
                lists.append({"asset": x.get("name", ""), "assetId": str(x.get("assetId")), "benchmark": bench,
                              "revision": s.get("revisionStr") or "", "site": site, "instance": inst,
                              "reviews": reviews(x, bench), "rules": rules(bench, s.get("revisionStr") or "")})
    except RuntimeError as e:
        return {"status": "error", "why": str(e)}
    return {"status": "found", "why": "", "collection": a["_col"].get("name", ""), "asset": a.get("name", ""), "checklists": lists}


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
