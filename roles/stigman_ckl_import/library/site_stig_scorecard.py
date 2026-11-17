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
  - 1.68.0, per collection, the counts from STIG Manager's metrics - what its own dashboard reads, quick on a big collection:
    GET /collections/ID/metrics/summary/collection (the assets; CAT I, II and III open - findings high, medium, low) and
    GET /collections/ID/metrics/summary (a row per asset and STIG: its findings by severity, its oldest review). CAT I open
    is the number of (rule, asset) pairs of severity high, as before.
  - An older STIG Manager without the metrics, or a grant without them: the findings by rule
    (GET /collections/ID/findings?aggregator=ruleId&acceptedOnly=false&projection=stigs), the assets and their STIGs, as 1.67.0.
  - Then, with C(hosts), the open rules and their STIGs (findings by rule, projection stigs; C(rules_timeout)) and each
    rule's hosts (projection assets; C(hosts_timeout)) - the slow calls on a big collection: too slow, the counts stay.
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
  hosts: {description: "Also read the open rules with their hosts (findings by rule, projection assets and stigs - the slow call on
          a big collection; 1.68.0: the counts come from the metrics). false: counts only.", type: bool, default: true}
  rules_timeout: {description: "Seconds for a collection's open rules (1.66.2; 1.68.0 default 600). Longer: its counts stay (Counts only).",
                  type: int, default: 600}
  hosts_timeout: {description: "Seconds for a collection's rules' hosts (1.68.0 default 600). Longer: its counts and rules stay.",
                  type: int, default: 600}
  texts: {description: "1.68.0: also read each open rule's check text and fix text (GET /stigs/rules/RULE, projections check and
          fix; once per rule, the rules open on the most hosts first). false: not read.", type: bool, default: true}
  text_timeout: {description: "Seconds for all the rules' texts together (1.68.0). Longer: the rules not read by then have no text.",
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
                why not listed), timing ({counts, assets, hosts}: seconds), error ('' = read), asset_ids (1.68.1: {asset
                name: assetId} for the website's links)}"
  type: list
missing: {description: "The names of C(collections) that the client cannot read or that are not there.", type: list}
timings: {description: "1.67.0: one line per collection - the seconds of each call (counts, assets, hosts).", type: list}
rule_texts: {description: "1.68.0: {SV-257985: {rule (the rule id read), check, fix}} - each open rule's check text and fix text.", type: dict}
texts: {description: "1.68.0: how the texts were read - {asked, read, seconds, slow (the time ran out), error ('' = read), missing
        (rules STIG Manager had no text for)}.", type: dict}
'''

import json
import re
import ssl
import threading
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import quote, urlencode
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

SV_RE = re.compile(r"(SV-\d+)")
CAT = {"high": 1, "medium": 2, "low": 3}
TEXT_MAX = 8000                 # characters of a rule's check text or fix text kept (DISA's longest are about 5000)
TEXT_RULES = 1000               # rules whose texts are read at most (the most hosts first)
TEXT_WORKERS = 4                # requests at a time for the texts


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


def asset_count(client, cid):
    try:
        m = client.get("/collections/%s/metrics/summary/collection" % cid)
        if isinstance(m, dict) and isinstance(m.get("assets"), int):
            return m["assets"]
    except RuntimeError:
        pass                                    # an older STIG Manager, or no grant for the metrics: count the assets
    rows = client.get("/assets", collectionId=cid)
    return len(rows or [])


def asset_stigs(client, cid, wait=None, ids=None):
    """1.67.0: {asset name: [benchmark ids]} of a collection (GET /assets?collectionId=..&projection=stigs) - the scorecard
    counts an asset under each platform it has a STIG of. {} when they cannot be read (the platforms then count no
    assets). wait: seconds for this request (1.68.0: when the metrics per checklist were not read), else the client's."""
    try:
        rows = client.get("/assets", wait=wait, collectionId=cid, projection="stigs")
    except RuntimeError:
        return {}
    out = {}
    if ids is not None:                         # 1.68.1: each asset's id too
        ids.update(ids_of(rows))
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


def ids_of(rows):
    """1.68.1: {asset name: assetId} from STIG Manager's rows - the metrics (a row per asset and STIG) and the findings with
    projection=assets (each rule's assets). The website's links open an asset's review with it (stigman_link_style open)."""
    out = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for a in [r] + [x for x in r.get("assets") or [] if isinstance(x, dict)]:
            if a.get("name") and str(a.get("assetId") or "").isdigit():
                out.setdefault(str(a["name"]), str(a["assetId"]))
    return out


def _n(v):
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def sev_of(m, key):
    """1.70.0: STIG Manager's metrics of one kind by severity (assessmentsBySeverity, assessedBySeverity) -> [CAT I, II, III]
    (high, medium, low), or None when the metrics do not have it (an older STIG Manager)."""
    d = m.get(key) if isinstance(m, dict) else None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), int) for k in ("high", "medium", "low")):
        return None
    return [max(0, d["high"]), max(0, d["medium"]), max(0, d["low"])]


def metrics_of(rows):
    """1.68.0: GET /collections/ID/metrics/summary (a row per asset and STIG) -> [{asset, stig, cat1, cat2, cat3, min_ts,
    max_ts}]: CAT I is the findings STIG Manager counts high, II medium, III low; min_ts its oldest review."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not (r.get("name") or r.get("assetId")):
            continue
        m = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        f = m.get("findings") if isinstance(m.get("findings"), dict) else {}
        rec = {"asset": str(r.get("name") or r.get("assetId")), "stig": str(r.get("benchmarkId") or ""),
               "cat1": _n(f.get("high")), "cat2": _n(f.get("medium")), "cat3": _n(f.get("low")),
               "min_ts": str(m.get("minTs") or ""), "max_ts": str(m.get("maxTs") or "")}
        a, d = sev_of(m, "assessmentsBySeverity"), sev_of(m, "assessedBySeverity")
        if a is not None and d is not None:             # 1.70.0: the CORA risk score's counts (a: rules, d: assessed), CAT I, II, III
            rec["cora_a"], rec["cora_d"] = a, d
        out.append(rec)
    return out


def read(p, client):
    """Each collection (1.68.0):
    1. The counts from STIG Manager's metrics - the ones its own dashboard reads, quick on a big collection:
       GET /collections/ID/metrics/summary/collection (the assets, CAT I/II/III open) and /metrics/summary (a row per asset
       and STIG). An older STIG Manager, or a grant without the metrics: 1.67.0's findings by rule (assetCount).
    2. The rules, each with its hosts and its STIG (findings by rule, projection assets and stigs) - the slow call, in a
       call of its own (hosts_timeout): too slow, the counts stay and hosts says why. hosts false: not asked.
    timing: the seconds of each call; counts_from: metrics or findings - one line each in the job output."""
    cols = client.get("/collections") or []
    mine, missing = wanted([c for c in cols if isinstance(c, dict) and c.get("collectionId") is not None], p["collections"])
    out = []
    for c in sorted(mine, key=lambda c: str(c.get("name", "")).lower()):
        cid = str(c["collectionId"])
        rec = {"id": cid, "name": str(c.get("name") or cid), "assets": 0, "cat1": 0, "cat2": 0, "cat3": 0, "rules": [], "error": "", "asset_ids": {},
               "asset_stigs": {}, "checklists": [], "hosts": "off", "counts_from": "", "timing": {}}
        t = time.time()
        try:
            tot = client.get("/collections/%s/metrics/summary/collection" % cid)
            if not (isinstance(tot, dict) and isinstance(tot.get("metrics"), dict)):
                raise RuntimeError("the metrics are not in the form this release knows")
            f = tot["metrics"].get("findings") if isinstance(tot["metrics"].get("findings"), dict) else {}
            rec.update(assets=_n(tot.get("assets")), cat1=_n(f.get("high")), cat2=_n(f.get("medium")), cat3=_n(f.get("low")),
                       counts_from="metrics")
            a, d = sev_of(tot["metrics"], "assessmentsBySeverity"), sev_of(tot["metrics"], "assessedBySeverity")
            if a is not None and d is not None:        # 1.70.0: the CORA risk score's counts, no more calls
                rec["cora_a"], rec["cora_d"] = a, d
            try:                                       # a row per asset and STIG: not read, the totals stay (no host counts)
                rows = client.get("/collections/%s/metrics/summary" % cid)
                if not isinstance(rows, list):
                    raise RuntimeError("the metrics are not in the form this release knows")
                rec["checklists"] = metrics_of(rows)
                rec["asset_ids"] = ids_of(rows)
            except RuntimeError as e:
                rec["checklists_note"] = "host counts not read (%s)" % str(e)[:300]
            st = {}
            for x in rec["checklists"]:
                st.setdefault(x["asset"], set()).add(x["stig"])
            rec["asset_stigs"] = dict((a, sorted(b - {""})) for a, b in st.items())
            rec["timing"]["metrics"] = round(time.time() - t, 2)
            if rec.get("checklists_note"):             # each asset's STIGs (its platforms) the 1.67.0 way, its own limit
                t = time.time()
                rec["asset_stigs"] = asset_stigs(client, cid, wait=p.get("hosts_timeout") or None, ids=rec["asset_ids"])
                rec["timing"]["assets"] = round(time.time() - t, 2)
        except RuntimeError as e:
            rec["timing"]["metrics"] = round(time.time() - t, 2)
            rec["metrics_error"] = str(e)[:300]
            step, t = "counts", time.time()            # 1.67.0's way: the findings by rule (assetCount), then the assets
            try:
                rows = client.get("/collections/%s/findings" % cid, wait=p.get("rules_timeout") or None,
                                  aggregator="ruleId", acceptedOnly="false", projection="stigs")
                rec["rules"], cats = findings_of(rows)
                rec.update(cat1=cats[1], cat2=cats[2], cat3=cats[3], counts_from="findings")
                rec["timing"]["counts"] = round(time.time() - t, 2)
                step, t = "assets", time.time()
                rec["assets"] = asset_count(client, cid)
                rec["asset_stigs"] = asset_stigs(client, cid, ids=rec["asset_ids"])
                rec["timing"]["assets"] = round(time.time() - t, 2)
            except RuntimeError as e2:
                rec["error"] = str(e2)
                rec["timing"][step] = round(time.time() - t, 2)
        open_ = rec["cat1"] + rec["cat2"] + rec["cat3"]
        rec["rules_read"] = rec["counts_from"] == "findings" or (not rec["error"] and not open_)
        if p.get("hosts", True) and not rec["error"] and open_ and rec["counts_from"] == "metrics":
            t = time.time()                            # the open rules and their STIGs, with their own limit
            try:
                rec["rules"], cats = findings_of(client.get("/collections/%s/findings" % cid, wait=p.get("rules_timeout") or None,
                                                            aggregator="ruleId", acceptedOnly="false", projection="stigs"))
                rec["rules_read"] = True
            except RuntimeError as e:
                rec["rules_note"] = ("rules not read (STIG Manager was slow: over %ss, stigman_scorecard_rules_timeout)" % p.get("rules_timeout")
                                     if "in time" in str(e) else "rules not read (%s)" % e)
                rec["hosts"] = "hosts not listed (%s)" % rec["rules_note"][len("rules not read ("):].rstrip(")")
            rec["timing"]["rules"] = round(time.time() - t, 2)
        if p.get("hosts", True) and not rec["error"] and rec["rules"] and rec["rules_read"]:
            t = time.time()                            # each rule's hosts, with their own limit
            try:
                hrows = client.get("/collections/%s/findings" % cid, wait=p.get("hosts_timeout") or None,
                                   aggregator="ruleId", acceptedOnly="false", projection="assets")
                names = hosts_of(hrows)
                rec["asset_ids"] = dict(ids_of(hrows), **(rec.get("asset_ids") or {}))
                for r in rec["rules"]:
                    r["assets"] = names.get(r["rule"], [])
                rec["hosts"] = "listed"
            except RuntimeError as e:
                rec["hosts"] = ("hosts not listed (STIG Manager was slow: over %ss, stigman_scorecard_hosts_timeout)" % p.get("hosts_timeout")
                                if "in time" in str(e) else "hosts not listed (%s)" % e)
            rec["timing"]["hosts"] = round(time.time() - t, 2)
        elif not rec["error"] and not open_:
            rec["hosts"] = "listed"                 # no open finding: no rule, no host to list
        out.append(rec)
    return out, missing


def _clean(t):
    """A rule's text as STIG Manager keeps it: its line ends kept, the spaces at a line's end and the runs of empty lines
    gone, capped at TEXT_MAX."""
    lines = [ln.rstrip() for ln in str(t or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return t[:TEXT_MAX] + ("\n[... cut]" if len(t) > TEXT_MAX else "")


def text_of(ans):
    """GET /stigs/rules/RULE?projection=check&projection=fix -> {check, fix}: STIG Manager 1.6 gives check {system, content}
    and fix {fixref, text}; a list of them (checks, fixes) is read too. An unknown rule: an empty answer, no text."""
    a = ans if isinstance(ans, dict) else {}

    def many(one, more, key):
        xs = a.get(more) if isinstance(a.get(more), list) else ([a[one]] if isinstance(a.get(one), dict) else [])
        return _clean("\n\n".join(str((x or {}).get(key) or "").strip() for x in xs if isinstance(x, dict) and (x or {}).get(key)))
    return {"check": many("check", "checks", "content"), "fix": many("fix", "fixes", "text")}


def rule_texts(client, cols, budget, most=TEXT_RULES, workers=TEXT_WORKERS):
    """1.68.0: each open rule's check and fix text, once per rule (SV number: a rule open in two collections, or under two
    revisions' ids, is read once), the rules open on the most hosts first. budget: seconds for them all - a request gets
    what is left; when it runs out, the rules not read have no text. A 401 or 403 stops it (the client cannot read STIGs:
    the scope stig-manager:stig:read). -> ({sv: {rule, check, fix}}, {asked, read, seconds, slow, error, missing})"""
    hosts, first = {}, {}
    for c in cols or []:
        for r in c.get("rules") or []:
            if isinstance(r, dict) and r.get("sv") and r.get("rule"):
                hosts[r["sv"]] = hosts.get(r["sv"], 0) + int(r.get("count") or 0)
                first.setdefault(r["sv"], r["rule"])
    order = sorted(hosts, key=lambda sv: (-hosts[sv], sv))[:max(0, int(most))]
    out, info = {}, {"asked": len(order), "read": 0, "seconds": 0.0, "slow": False, "error": "", "missing": 0}
    if not order or budget <= 0:
        info["slow"] = bool(order)
        return out, info
    start = time.time()
    end = start + budget
    todo = list(order)
    lock = threading.Lock()

    def work():
        while True:
            with lock:
                if not todo or info["error"] or info["slow"]:
                    return
                sv = todo.pop(0)
            left = end - time.time()
            if left <= 0.5:
                with lock:
                    info["slow"] = True
                return
            try:
                t = text_of(client.get("/stigs/rules/%s" % quote(first[sv], safe=""), wait=min(left, client.p["timeout"]),
                                       projection=["check", "fix"]))
            except RuntimeError as e:
                with lock:
                    if re.search(r"HTTP 40[13]\b", str(e)):
                        info["error"] = "%s - the client needs the scope stig-manager:stig:read" % str(e)[:200]
                    elif "in time" in str(e) and time.time() >= end - 1:
                        info["slow"] = True
                    else:
                        info["missing"] += 1
                continue
            with lock:
                if t["check"] or t["fix"]:
                    out[sv] = dict(t, rule=first[sv])
                else:
                    info["missing"] += 1
    threads = [threading.Thread(target=work) for i in range(max(1, min(int(workers), len(order))))]
    for th in threads:
        th.daemon = True                     # a request still waiting when the time is up is not waited for
        th.start()
    for th in threads:
        th.join(max(0.1, end - time.time() + 1))
    with lock:
        if any(th.is_alive() for th in threads):
            info["slow"] = True
        info["read"] = len(out)
        info["seconds"] = round(time.time() - start, 2)
        return dict(out), dict(info)


def text_line(info):
    """The job output's line for the texts (1.68.0)."""
    i = info or {}
    if not i.get("asked"):
        return ""
    return "Check and fix text: %d of %d rules in %.1f s%s%s%s" % (
        _int(i.get("read")), _int(i.get("asked")), float(i.get("seconds") or 0),
        "; the time ran out (stigman_scorecard_text_timeout)" if i.get("slow") else "",
        "; %d without text in STIG Manager" % _int(i.get("missing")) if i.get("missing") else "",
        "; " + str(i["error"]) if i.get("error") else "")


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def timing_lines(cols):
    """One line per collection: where its counts came from and how long each call took (the slow one stands out)."""
    out = []
    for c in cols or []:
        tm = c.get("timing") or {}
        parts = ["%s %.1f s" % (k, tm[k]) for k in ("metrics", "counts", "assets", "rules", "hosts") if k in tm]
        if c.get("rules_read") is False and not c.get("error") and c.get("rules_note"):
            parts.append("Counts only (%s)" % c["rules_note"])
        if c.get("checklists_note") and not c.get("error"):
            parts.append(c["checklists_note"])
        src = {"metrics": "counts from the metrics", "findings": "counts from the findings (no metrics: %s)" % str(c.get("metrics_error") or "")[:120]}.get(
            c.get("counts_from"), "")
        out.append("%s (id %s): %s%s%s" % (c.get("name"), c.get("id"), ", ".join(parts) or "not read", ("; " + src) if src else "",
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
        hosts_timeout=dict(type="int", default=600),
        rules_timeout=dict(type="int", default=600),
        texts=dict(type="bool", default=True),
        text_timeout=dict(type="int", default=120),
    ), supports_check_mode=True)
    p = module.params
    res = {"changed": False, "ok": False, "error": "", "collections": [], "missing": [], "timings": [],
           "rule_texts": {}, "texts": {}, "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
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
    if p["texts"]:                              # 1.68.0: the rules' check and fix text, after the counts (never fails the read)
        res["rule_texts"], res["texts"] = rule_texts(client, [c for c in res["collections"] if not c.get("error")], p["text_timeout"])
        if text_line(res["texts"]):
            res["timings"].append(text_line(res["texts"]))
    res["ok"] = True
    module.exit_json(**res)


if __name__ == "__main__":
    main()
