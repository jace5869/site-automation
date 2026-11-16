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
    (GET /collections/ID/findings?aggregator=ruleId&acceptedOnly=false&projection=stigs: the counts) and the number of assets
    (GET /collections/ID/metrics/summary/collection; an older STIG Manager without it - GET /assets?collectionId=ID), and
    1.67.0 each asset's STIGs (GET /assets?collectionId=ID&projection=stigs) for the scorecard's platforms. Then, with
    C(hosts), each rule's hosts (the same findings call with projection=assets - slow on a big collection) under its own
    C(hosts_timeout): a collection whose host list is slow keeps its counts.
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
  hosts: {description: "1.67.0: also list each open rule's hosts (a second findings call with projection=assets - the slow one on
          a big collection). false: counts only.", type: bool, default: true}
  hosts_timeout: {description: "1.67.0: seconds for that call. A collection whose host list takes longer keeps its counts.",
                  type: int, default: 120}
author: site automation
'''

RETURN = r'''
ok: {description: The token and the list of collections were read., type: bool}
error: {description: "Why nothing could be read, in plain words ('' = read).", type: str}
read_at: {description: "When this was read (UTC, 2026-11-13T06:00:05Z).", type: str}
collections:
  description: "Per collection, by name: {id, name, assets, cat1, cat2, cat3 (open findings: rule and asset pairs), rules: [{rule
                (its id), sv (SV-257985), cat (1, 2 or 3), version (the STIG ID), title, assets: [names], stig (1.67.0: the
                STIG it is in, its benchmark id; '' when STIG Manager does not say)}], asset_stigs (1.67.0: {asset name:
                [benchmark ids]}; {} when the assets could not be read with their STIGs), hosts (1.67.0: listed, off, or
                why not listed), timing ({counts, assets, hosts}: seconds), error ('' = read)}"
  type: list
missing: {description: "The names of C(collections) that the client cannot read or that are not there.", type: list}
timings: {description: "1.67.0: one line per collection - the seconds of each call (counts, assets, hosts).", type: list}
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

    def _open(self, req, timeout=None):
        try:
            resp = urlopen(req, timeout=timeout or self.p["timeout"], context=self.ctx)
            return json.loads(resp.read().decode("utf-8") or "null")
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("HTTP %d from %s: %s" % (e.code, req.get_full_url().split("?")[0], body.strip() or e.reason))
        except URLError as e:
            raise RuntimeError("cannot reach %s: %s" % (req.get_full_url().split("?")[0], e.reason))
        except (OSError, TimeoutError) as e:     # a read that timed out or a dropped connection: not a URLError (1.67.0)
            raise RuntimeError("no answer from %s in time (%s): %s" % (req.get_full_url().split("?")[0],
                                                                       "a slow answer: raise check_stig_stigman_timeout" if isinstance(e, TimeoutError) or "timed out" in str(e) else "the connection dropped", e))
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

    def get(self, path, wait=None, **query):
        """GET path?query; wait: seconds for this request (1.67.0: the host lists' own timeout), else the client's."""
        q = urlencode(sorted(query.items()), doseq=True)
        req = Request(self.api + path + ("?" + q if q else ""),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return self._open(req, wait)



def _coll_hits(cols, w, name_of, id_of):
    """The collections an entry names: its exact name (any case) or id first; only when none is exact, a pattern - * and ?
    only, so a name with [ ] (\"[PROD] Servers\") is a name, never a character class."""
    import fnmatch
    w = str(w).strip()
    exact = [c for c in cols if str(name_of(c) or "").lower() == w.lower() or str(id_of(c)) == w]
    if exact or not any(ch in w for ch in "*?"):
        return exact
    pat = w.lower().replace("[", "[[]")
    return [c for c in cols if fnmatch.fnmatchcase(str(name_of(c) or "").lower(), pat)]


def wanted(cols, names):
    """(the collections to read, the names not found): names are collection names or ids, any case, * and ? allowed;
    [] or ['*'] = all. An entry starting with ! leaves that collection out (1.67.0): ['*', '!48', '!Archived*'] = all but those."""
    names = [str(n).strip() for n in names or [] if str(n).strip()]
    skip = [n[1:].strip() for n in names if n.startswith("!")]
    keep = [n for n in names if not n.startswith("!")]

    def hits(n):
        return _coll_hits(cols, n, lambda c: c.get("name", ""), lambda c: c.get("collectionId"))
    missing = []
    if not keep or "*" in keep:
        out = list(cols)
    else:
        out = []
        for n in keep:
            h = hits(n)
            if not h:                           # a typo or a pattern that matches nothing: said
                missing.append(n)
            for c in h:
                if c not in out:
                    out.append(c)
    for n in skip:
        h = hits(n)
        if not h:
            missing.append("!" + n)
        out = [c for c in out if c not in h]
    return out, missing


def _bench(x):
    """A STIG as STIG Manager gives it in a projection: its benchmark id, or {benchmarkId, ...} -> the benchmark id."""
    if isinstance(x, dict):
        return str(x.get("benchmarkId") or "")
    return str(x or "")


def findings_of(rows):
    """STIG Manager's findings by rule -> (rules, {1: n, 2: n, 3: n}): a rule's assets by name, each once; a finding
    without the assets projection counts its assetCount. 1.67.0: a rule's STIG (projection stigs; a rule is in one STIG -
    the first named when STIG Manager names more)."""
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
        stigs = sorted(set(_bench(s) for s in f.get("stigs") or [] if _bench(s)))
        rules.append({"rule": str(f["ruleId"]), "sv": sv_of(f["ruleId"]), "cat": cat, "version": str(f.get("version") or ""),
                      "title": " ".join(str(f.get("title") or "").split()), "assets": names, "count": n,
                      "stig": stigs[0] if stigs else ""})
    rules.sort(key=lambda r: (r["cat"], -r["count"], r["sv"]))
    return rules, cats


def metric_cats(m):
    """STIG Manager's metrics summary -> {1: high, 2: medium, 3: low} (the open findings by category), or None when the
    answer has no findings counts (an older STIG Manager, or no grant for the metrics)."""
    if not isinstance(m, dict):
        return None
    mm = m.get("metrics") if isinstance(m.get("metrics"), dict) else m
    f = mm.get("findings") if isinstance(mm, dict) else None
    if not isinstance(f, dict) or not all(k in f for k in ("high", "medium", "low")):
        return None
    try:
        return {1: int(f["high"]), 2: int(f["medium"]), 3: int(f["low"])}
    except (TypeError, ValueError):
        return None


def asset_count(client, cid):
    try:
        m = client.get("/collections/%s/metrics/summary/collection" % cid)
        if isinstance(m, dict) and isinstance(m.get("assets"), int):
            return m["assets"]
    except RuntimeError:
        pass                                    # an older STIG Manager, or no grant for the metrics: count the assets
    rows = client.get("/assets", collectionId=cid)
    return len(rows or [])


def asset_stigs(client, cid):
    """1.67.0: {asset name: [benchmark ids]} of a collection (GET /assets?collectionId=..&projection=stigs) - the scorecard
    counts an asset under each platform it has a STIG of. {} when they cannot be read (the platforms then count no
    assets)."""
    try:
        rows = client.get("/assets", collectionId=cid, projection="stigs")
    except RuntimeError:
        return {}
    out = {}
    for a in rows or []:
        if isinstance(a, dict) and (a.get("name") or a.get("assetId")):
            out[str(a.get("name") or a.get("assetId"))] = sorted(set(_bench(s) for s in a.get("stigs") or [] if _bench(s)))
    return out


def hosts_of(rows):
    """1.67.0: the findings with projection=assets -> {rule id: [asset names]} (each once, sorted)."""
    out = {}
    for f in rows or []:
        if isinstance(f, dict) and f.get("ruleId"):
            out[str(f["ruleId"])] = sorted(set(str(a.get("name") or a.get("assetId") or "") for a in f.get("assets") or []
                                               if isinstance(a, dict)) - {""}, key=lambda s: s.lower())
    return out


def read(p, client):
    """Each collection: 1.67.0 - the counts first (findings by rule with assetCount and the rule's STIG: the cheap call), the
    assets and their STIGs, then - with hosts - each rule's hosts in a call of their own (hosts_timeout): a collection
    whose host list is slow keeps its counts, hosts says why. timing: the seconds of each call, for the job output."""
    cols = client.get("/collections") or []
    mine, missing = wanted([c for c in cols if isinstance(c, dict) and c.get("collectionId") is not None], p["collections"])
    out = []
    for c in sorted(mine, key=lambda c: str(c.get("name", "")).lower()):
        cid = str(c["collectionId"])
        rec = {"id": cid, "name": str(c.get("name") or cid), "assets": 0, "cat1": 0, "cat2": 0, "cat3": 0, "rules": [], "error": "",
               "asset_stigs": {}, "hosts": "off", "timing": {}}
        step = "metrics"
        t = time.time()
        # 1.66.2: the counts from STIG Manager's own metrics first (pre-counted, fast on any collection); the rules a
        # second read with its own limit - too slow: the counts stay, "Counts only" (rules_read False)
        mcats = None
        try:
            m = client.get("/collections/%s/metrics/summary/collection" % cid)
            mcats = metric_cats(m)
            if mcats is not None:
                rec.update(cat1=mcats[1], cat2=mcats[2], cat3=mcats[3], counts_from="metrics")
                if isinstance(m, dict) and isinstance(m.get("assets"), int):
                    rec["assets"] = m["assets"]
        except RuntimeError:
            pass
        rec["timing"]["metrics"] = round(time.time() - t, 2)
        rec["rules_read"] = False
        step = "counts"
        try:
            t = time.time()
            try:
                rows = client.get("/collections/%s/findings" % cid, wait=p.get("rules_timeout") or None, aggregator="ruleId",
                                  acceptedOnly="false", projection="stigs")
                rec["rules"], cats = findings_of(rows)
                rec["rules_read"] = True
                if mcats is None:
                    rec.update(cat1=cats[1], cat2=cats[2], cat3=cats[3], counts_from="findings")
            except RuntimeError as e:
                if mcats is None:
                    raise
                rec["rules_note"] = "rules not read (counts from STIG Manager's metrics): %s" % e
            rec["timing"]["counts"] = round(time.time() - t, 2)
            step, t = "assets", time.time()
            if not rec.get("assets"):
                rec["assets"] = asset_count(client, cid)
            rec["asset_stigs"] = asset_stigs(client, cid)
            rec["timing"]["assets"] = round(time.time() - t, 2)
        except RuntimeError as e:
            rec["error"] = str(e)
            rec["timing"][step] = round(time.time() - t, 2)
        if p.get("hosts", True) and not rec["error"] and rec["rules"] and rec.get("rules_read"):
            t = time.time()
            try:
                names = hosts_of(client.get("/collections/%s/findings" % cid, wait=p.get("hosts_timeout") or None,
                                            aggregator="ruleId", acceptedOnly="false", projection="assets"))
                for r in rec["rules"]:
                    r["assets"] = names.get(r["rule"], [])
                rec["hosts"] = "listed"
            except RuntimeError as e:
                rec["hosts"] = ("hosts not listed (STIG Manager was slow: over %ss, stigman_scorecard_hosts_timeout)" % p.get("hosts_timeout")
                                if "in time" in str(e) else "hosts not listed (%s)" % e)
            rec["timing"]["hosts"] = round(time.time() - t, 2)
        elif rec["error"] == "" and not rec["rules"]:
            rec["hosts"] = "listed"                 # no open finding: no host to list
        out.append(rec)
    return out, missing


def timing_lines(cols):
    """One line per collection: how long each call took (the slow one stands out)."""
    out = []
    for c in cols or []:
        tm = c.get("timing") or {}
        parts = ["%s %.1f s" % (k, tm[k]) for k in ("metrics", "counts", "assets", "hosts") if k in tm]
        if c.get("rules_read") is False and not c.get("error"):
            parts.append("Counts only (the rules were too slow, stigman_scorecard_rules_timeout)")
        out.append("%s (id %s): %s%s" % (c.get("name"), c.get("id"), ", ".join(parts) or "not read",
                                         "; " + c["error"][:200] if c.get("error") else
                                         ("; " + c["hosts"] if str(c.get("hosts", "")).startswith("hosts not") else "")))
    return out


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
        hosts=dict(type="bool", default=True),
        hosts_timeout=dict(type="int", default=120),
        rules_timeout=dict(type="int", default=180),
    ), supports_check_mode=True)
    p = module.params
    res = {"changed": False, "ok": False, "error": "", "collections": [], "missing": [], "timings": [],
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
        res["timings"] = timing_lines(res["collections"])
    except RuntimeError as e:
        res["error"] = "STIG Manager could not be read: %s" % e
        module.exit_json(**res)
    res["ok"] = True
    module.exit_json(**res)


if __name__ == "__main__":
    main()
