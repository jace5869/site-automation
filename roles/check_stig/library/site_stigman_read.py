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
      asset and to its web site and database assets (<asset>-<site>-<instance>), each a checklist, create: {name, fqdn} - the
      asset to create when none is found (workflow 37, check_stig_update_create_assets; with collection and rules): looked
      for in every collection the client can read, then the collection's revision of the STIG and its rules read}]; [] with
      texts (1.73.0): only the texts are read"
    type: list
    elements: dict
    default: []
  texts:
    description: "1.73.0: the check text and fix text of these rules - [{benchmark, revision ('' = latest), rules: [SV-257985, ...]}]:
      one read of each revision's rules (GET /stigs/BENCHMARK/revisions/REVISION/rules with the projections check and fix -
      the client's scope stig-manager:stig:read), only the rules asked kept. With hosts [] only the texts are read."
    type: list
    elements: dict
    default: []
  text_timeout: {description: "1.73.0b: seconds for all the texts' reads together; the first read that fails stops them.", type: int, default: 120}
  collections: {description: "Only these collections are searched ([] = every collection the client can read).", type: list, elements: str, default: []}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
  protect_words:
    description: "1.69.1: a review whose detail or comment says one of these (whole words, any case) is marked protect - never
      written by site-automation ([] = none)."
    type: list
    elements: str
    default: [RA, Risk Acceptance, POAM, POA&M]
author: site automation
'''

RETURN = r'''
hosts:
  description: "Per host (per id): {status: found / not_found / no_stig (the asset is there, without this STIG) / several /
                no_benchmark / error, why, collection, collectionId, asset, assetId, revision, reviews: {SV-257985: {result,
                status, ts, rule, comment (true: it has one), version, severity, title, ours (1.69.1: our line in its comment),
                ours_said (the result that line gave), protect (why it is never written, or empty), engine (1.77.0: {product, type} of a
                scanner's import, null for a person's review)}}, earlier: {SV-257985: the same, and revision} (with rules: true -
                a review of the rule in an earlier revision of the STIG, whose check changed since:
                STIG Manager does not carry it to the revision in use), rules (with rules: true): {SV-257985: {v, rule, version,
                severity, title}}, create (with create, not found anywhere: {name, fqdn, collection, collectionId, benchmark,
                revision} - and rules)}; with every: true {status, why, collection, collectionId, asset, checklists: [{asset, assetId,
                benchmark, revision, site, instance, reviews, earlier, rules}]}"
  type: dict
  returned: always
texts:
  description: "1.73.0 (texts): {SV-257985: {check, fix}} - the rules STIG Manager has a text for; texts_info: {asked, read, error}."
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
import threading
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode, quote
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.site_redact import head, redact, run_main
from ansible.module_utils.site_stig_comment import split as comment_split, what_it_said, protected, PROTECT_WORDS

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


def earlier_of(rows, bench, current, now):
    """The asset's reviews of every revision (rules=all, projection=stigs: each review with the revisions its rule is in)
    -> the reviews of rules only in other revisions than the one in use (current): a rule DISA changed has a new id, and
    STIG Manager keeps the review of the old one apart - {SV id: record and the newest of those revisions ('V2R8')}; a
    rule with a review in the revision in use (now) is left out. (rules=not-default-mapped answers none of them: proven
    on STIG Manager 1.6.17, tests/stig/run_stigman_real_test.sh.)"""
    out = {}
    for r in rows or []:
        mine = [s for s in (r.get("stigs") or []) if s.get("benchmarkId") == bench]
        if any(s.get("isDefault") or s.get("revisionStr") == current for s in mine):
            continue
        revs = [s.get("revisionStr") or "" for s in mine]
        rev = max([v for v in revs if v and v != current] or [""], key=revision_key)
        for k, rec in reviews_of([r]).items():
            if k not in now and (k not in out or revision_key(rev) > revision_key(out[k]["revision"])):
                out[k] = dict(rec, revision=rev)
    return out


def revision_key(rev):
    """'V2R10' -> (2, 10), for order ('' first)."""
    m = re.match(r"V(\d+)R(\d+)$", (rev or "").strip().upper())
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def engine_of(eng):
    """STIG Manager's resultEngine -> {product, type}, or None (a person's review)."""
    if not isinstance(eng, dict) or not (eng.get("product") or eng.get("type")):
        return None
    # 1.77.0h: the server's own words - redacted (the cross-check needs only whether there is an engine)
    return {"product": redact(str(eng.get("product") or ""))[:120], "type": redact(str(eng.get("type") or ""))[:60]}


def reviews_of(rows, words=None):
    """STIG Manager's reviews of one asset -> {SV id: record}; a review that covers several revisions of a rule
    (ruleIds) counts for each of them. words: stigman_protect_words (None: the default list)."""
    out = {}
    for r in rows or []:
        rule = r.get("rule") or {}
        st = r.get("status") or {}
        raw = comment_split(r.get("comment"))[0]
        # 1.77.0i (Codex P1): our line as shown is redacted whole, then cut; what it said is read from the line as written
        ours = redact(raw, backstop=False)[:600]
        rec = {"result": r.get("result") or "", "status": st.get("label", "") if isinstance(st, dict) else str(st),
               "ts": r.get("ts") or r.get("touchTs") or "", "rule": r.get("ruleId") or "", "comment": bool(r.get("comment")),
               "version": rule.get("version") or "", "severity": rule.get("severity") or "", "title": rule.get("title") or "",
               # 1.69.1: our line in its comment (an earlier run's - the STIG recheck's or the import's) and the result it gave:
               # a review we wrote as Open that is not Open now was changed by a person after us
               "ours": ours, "ours_said": what_it_said(raw[:600]),
               # 1.69.1: a review whose detail or comment says RA, Risk Acceptance, POAM or POA&M (stigman_protect_words)
               # is never written - why, or ''
               "protect": protected(words, r.get("detail"), r.get("comment")),
               # 1.77.0: what made the result - a scanner's import ({product, type}: Evaluate-STIG, OpenSCAP ...) or a
               # person (None). The answer keys' cross-check counts a person only, never the scan's own import.
               "engine": engine_of(r.get("resultEngine"))}
        for rid in [r.get("ruleId")] + list(r.get("ruleIds") or []):
            k = sv_of(rid)
            if k and k not in out:
                out[k] = rec
    return out


def check_inputs(p):
    """1.75.2: '' (None, or only spaces) for good inputs, else why not: an empty token URL, client id or secret (the 'STIG Manager API'
    credential not attached), or a URL that is not http(s) - said up front, never a urllib traceback ('unknown url type')."""
    miss = [n for n, k in (("token URL", "token_url"), ("client id", "client_id"), ("secret", "client_secret")) if not str(p.get(k) or "").strip()]
    if miss:
        return ("STIG Manager: no %s - attach the 'STIG Manager API' credential to this template (or set check_stig_stigman_token_url)"
                % " / ".join(miss))
    bad = [("token URL", p["token_url"]), ("API URL", p.get("api"))]
    for n, u in bad:
        if not re.match(r"^https?://[^/\s]+", str(u or "").strip(), re.I):
            return "STIG Manager: the %s '%s' is not an http(s) URL - check_stig_stigman_token_url / check_stig_stigman_api" % (n, str(u or "")[:80])
    return ""


class Client(object):
    def __init__(self, p):
        self.p = p
        self.api = p["api"].rstrip("/")
        if not p["validate_certs"]:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(cafile=p["ca_path"] or None)
        self.token = None

    def _open(self, req, wait=None, limit=None):
        try:
            resp = urlopen(req, timeout=wait or self.p["timeout"], context=self.ctx)
            if not limit:
                return json.loads(resp.read().decode("utf-8") or "null")
            # 1.73.0c: the body in parts, at most limit bytes (the texts: a revision's rules are a few MB)
            body, size = [], 0
            part = getattr(resp, "read1", None) or resp.read
            while True:
                b = part(65536)
                if not b:
                    break
                size += len(b)
                if size > limit:
                    raise RuntimeError("the answer of %s is over %g MB" % (req.get_full_url().split("?")[0], round(limit / 1048576.0, 1)))
                body.append(b)
            return json.loads(b"".join(body).decode("utf-8") or "null")
        except HTTPError as e:
            raw = e.read(65537)
            body = raw[:65536].decode("utf-8", "replace")
            if len(raw) > 65536:
                body = head(body, len(body) - 1)      # 1.77.0h: read in part - the line the read split is left out
            # 1.77.0d: what the server answered, its secrets removed (site_redact) - it reaches the job's output and
            # reports; 1.77.0h: the reason too (it is the server's text)
            raise RuntimeError("HTTP %d from %s: %s" % (e.code, req.get_full_url().split("?")[0],
                                                       redact(body.strip())[:300] or redact(str(e.reason or ""))[:200]))
        except URLError as e:
            raise RuntimeError("cannot reach %s: %s" % (req.get_full_url().split("?")[0], redact(str(e.reason))[:200]))
        except (OSError, TimeoutError) as e:     # a read that timed out or a dropped connection: not a URLError (1.67.0)
            raise RuntimeError("no answer from %s in time (%s): %s" % (req.get_full_url().split("?")[0],
                                                                       "a slow answer: raise check_stig_stigman_timeout" if isinstance(e, TimeoutError) or "timed out" in str(e) else "the connection dropped", redact(str(e))[:200]))
        except ValueError:
            raise RuntimeError("%s did not answer JSON" % req.get_full_url().split("?")[0])

    def login(self):
        bad = check_inputs(self.p)
        if bad:
            raise RuntimeError(bad)
        form = {"grant_type": "client_credentials", "client_id": self.p["client_id"], "client_secret": self.p["client_secret"]}
        if self.p["scope"]:
            form["scope"] = self.p["scope"]
        req = Request(self.p["token_url"], data=urlencode(form).encode("utf-8"),
                      headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        ans = self._open(req) or {}
        if not ans.get("access_token"):
            raise RuntimeError("the token endpoint gave no access_token")
        self.token = ans["access_token"]

    def get(self, path, wait=None, deadline=None, limit=None, **query):
        """wait: seconds a read may wait (the socket's timeout; None = timeout). 1.73.0c, the texts: deadline - a
        time.monotonic() the whole request and its body must be done by (a server that trickles its answer is cut off
        there: the request runs in a thread of its own, left behind when the time is up), limit - the body's most bytes."""
        q = urlencode(sorted(query.items()), doseq=True)                # projection=rule&projection=stigs
        req = Request(self.api + path + ("?" + q if q else ""),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        if deadline is None:
            return self._open(req, wait, limit)
        box = {}

        def work():
            try:
                box["ok"] = self._open(req, wait, limit)
            except Exception as e:           # handed to the caller as it was raised
                box["error"] = e
        th = threading.Thread(target=work)
        th.daemon = True                     # a request still going when the time is up is not waited for
        th.start()
        th.join(max(0.0, deadline - time.monotonic()))
        if th.is_alive():
            raise RuntimeError("no answer from %s in time (the texts' budget, check_stig_rule_texts_timeout)" % (self.api + path))
        if "error" in box:
            raise box["error"]
        return box.get("ok")


TEXT_BODY_MAX = 50 * 1048576   # 1.73.0c: bytes of a revision's rules with their texts, at most (a few MB in use)
TEXT_MAX = 8000                 # characters of a check or fix text kept (DISA's longest are about 5000), as the scorecard


def clean_text(t):
    """A rule's text as STIG Manager keeps it: its line ends kept, the spaces at a line's end and the runs of empty lines
    gone, capped at TEXT_MAX."""
    lines = [ln.rstrip() for ln in str(t or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return t[:TEXT_MAX] + ("\n[... cut]" if len(t) > TEXT_MAX else "")


def text_of(rule):
    """A rule as GET .../rules?projection=check&projection=fix gives it -> {check, fix}: STIG Manager 1.6 gives check
    {system, content} and fix {fixref, text}; a list of them (checks, fixes) is read too."""
    a = rule if isinstance(rule, dict) else {}

    def many(one, more, key):
        xs = a.get(more) if isinstance(a.get(more), list) else ([a[one]] if isinstance(a.get(one), dict) else [])
        return clean_text("\n\n".join(str((x or {}).get(key) or "").strip() for x in xs if isinstance(x, dict) and (x or {}).get(key)))
    return {"check": many("check", "checks", "content"), "fix": many("fix", "fixes", "text")}


def read_texts(want, client, budget=120, clock=None):
    """1.73.0: the check and fix text of the rules asked, one read per STIG revision -> ({sv: {check, fix}}, info). The
    texts are optional, so they never hold the report up (1.73.0b): all the reads together get budget seconds (each the
    time left, at most the request timeout), and the first that cannot be read - no answer in time, a dropped connection,
    an HTTP 5xx, a 401 or 403 (the scope stig-manager:stig:read) - stops them. A revision STIG Manager does not have
    (HTTP 404) gives no text and the next is read."""
    clock = clock or time.monotonic
    out, info = {}, {"asked": 0, "read": 0, "error": ""}
    end = clock() + max(1, int(budget))
    hard = time.monotonic() + max(1, int(budget))         # 1.73.0c: the whole of every request, its body too
    todo = []
    for w in want or []:
        bench, rev = str((w or {}).get("benchmark") or ""), str((w or {}).get("revision") or "") or "latest"
        rules = set(sv_of(x) for x in (w or {}).get("rules") or [] if sv_of(x))
        if bench and rules:
            todo.append((bench, rev, rules))
    info["asked"] = len(set().union(*[r for b, v, r in todo])) if todo else 0
    for bench, rev, rules in todo:
        left = end - clock()
        if left < 1:
            info["error"] = "not all read within check_stig_rule_texts_timeout (%ss)" % int(budget)
            break
        try:
            rows = client.get("/stigs/%s/revisions/%s/rules" % (quote(bench), quote(rev)), wait=min(left, client.p["timeout"]),
                              deadline=min(hard, time.monotonic() + left), limit=TEXT_BODY_MAX, projection=["check", "fix"]) or []
        except RuntimeError as e:
            if re.search(r"HTTP 404\b", str(e)):
                info["error"] = info["error"] or "STIG Manager has no %s %s" % (bench, rev)
                continue
            info["error"] = redact(str(e))[:200] + (" - the client needs the scope stig-manager:stig:read"
                                             if re.search(r"HTTP 40[13]\b", str(e)) else "")
            break
        for r in rows if isinstance(rows, list) else []:
            sv = sv_of((r or {}).get("ruleId"))
            if sv in rules and sv not in out:
                t = text_of(r)
                if t["check"] or t["fix"]:
                    out[sv] = t
    info["read"] = len(out)
    return out, info


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
                                     benchmarkId=bench, projection="rule"), p.get("protect_words"))

    def earlier(a, bench, rev, now):
        """The reviews of the rules' other revisions (what the recheck says beside a rule with no review in rev); {} when
        STIG Manager does not answer it - the recheck reads the same without them."""
        try:
            return earlier_of(client.get("/collections/%s/reviews/%s" % (quote(str(a["_col"]["collectionId"])), quote(str(a["assetId"]))),
                                         benchmarkId=bench, rules="all", projection=["rule", "stigs"]), bench, rev, now)
        except RuntimeError:
            return {}

    for h in p["hosts"]:
        host, bench = h.get("host", ""), h.get("benchmark") or ""
        hid = h.get("id") or host
        names = [n for n in (h.get("names") or [host]) if n]
        if h.get("every"):
            out[hid] = every(h, names, by_name, cols, assets, rules, reviews, earlier)
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
            out[hid] = {"status": "error", "why": redact(str(e))}
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
            if h.get("create") and h.get("collection"):
                out[hid].update(to_create(h, names, bench, search[0], cols, assets, rules, client))
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
            out[hid] = {"status": "error", "why": redact(str(e)), "collection": a["_col"].get("name"), "asset": a.get("name")}
            continue
        out[hid] = {"status": "found", "why": "", "collection": a["_col"].get("name", ""), "collectionId": str(a["_col"].get("collectionId", "")),
                     "asset": a.get("name", ""),
                     "assetId": str(a.get("assetId")), "benchmark": bench, "revision": rev, "reviews": rv}
        if rl is not None:
            out[hid]["rules"] = rl
            out[hid]["earlier"] = earlier(a, bench, rev, rv)
    return out


def to_create(h, names, bench, col, cols, assets, rules, client):
    """A host with no asset in its collection, to create behind the approval (workflow 37): {create: {name, fqdn, collection,
    collectionId, benchmark, revision}, rules} - or {why} when it is not to be created: an asset of the host's in another
    collection the client can read, or STIG Manager without the STIG. Reads only."""
    try:
        elsewhere = []
        for c in cols:
            if c["collectionId"] != col["collectionId"]:
                elsewhere += match_assets(names, assets(c, ""))
        if elsewhere:
            return {"why": "no asset in %s - but %s: not created (set check_stig_stigman_collection to that collection)" % (
                col["name"], ", ".join("%s is in %s" % (a.get("name"), a["_col"].get("name")) for a in elsewhere))}
        # the revision the asset will get: the collection's (it may be pinned), else the newest STIG Manager has
        rev = ""
        for s in client.get("/collections/%s/stigs" % quote(str(col["collectionId"]))) or []:
            if s.get("benchmarkId") == bench:
                rev = s.get("revisionStr") or ""
        if not rev:
            rev = (client.get("/stigs/%s/revisions/latest" % quote(bench)) or {}).get("revisionStr") or ""
        rl = rules(bench, rev)
    except RuntimeError as e:
        why = redact(str(e))
        if "HTTP 404" in why:
            why = "STIG Manager has no %s - import the STIG first" % bench
        return {"why": "no asset in %s, and none created: %s" % (col["name"], why)}
    if not rl:
        return {"why": "no asset in %s, and none created: %s %s has no rules in STIG Manager" % (col["name"], bench, rev or "")}
    c = h["create"] or {}
    return {"why": "no asset in %s: one to create once approved" % col["name"], "rules": rl,
            "create": {"name": c.get("name") or names[0], "fqdn": c.get("fqdn") or "", "collection": col.get("name", ""),
                       "collectionId": str(col["collectionId"]), "benchmark": bench, "revision": rev}}


def every(h, names, by_name, cols, assets, rules, reviews, earlier):
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
                rv = reviews(x, bench)
                lists.append({"asset": x.get("name", ""), "assetId": str(x.get("assetId")), "benchmark": bench,
                              "revision": s.get("revisionStr") or "", "site": site, "instance": inst,
                              "reviews": rv, "earlier": earlier(x, bench, s.get("revisionStr") or "", rv),
                              "rules": rules(bench, s.get("revisionStr") or "")})
    except RuntimeError as e:
        return {"status": "error", "why": redact(str(e))}
    return {"status": "found", "why": "", "collection": a["_col"].get("name", ""), "collectionId": str(a["_col"].get("collectionId", "")),
            "asset": a.get("name", ""), "checklists": lists}


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        hosts=dict(type="list", elements="dict", default=[]),
        texts=dict(type="list", elements="dict", default=[]),
        text_timeout=dict(type="int", default=120),
        collections=dict(type="list", elements="str", default=[]),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
        protect_words=dict(type="list", elements="str", default=list(PROTECT_WORDS)),
    ), supports_check_mode=True)
    p = module.params
    bad = check_inputs(p)
    if bad:
        module.fail_json(msg=bad)
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        module.exit_json(changed=False, hosts={}, texts={}, texts_info={"asked": 0, "read": 0, "error": redact(str(e))[:200]},
                         error="no token for the client %s: %s" % (p["client_id"], redact(str(e))))
    texts, tinfo = read_texts(p["texts"], client, p["text_timeout"]) if p["texts"] else ({}, {"asked": 0, "read": 0, "error": ""})
    try:
        hosts = read(p, client) if p["hosts"] else {}
    except RuntimeError as e:
        module.exit_json(changed=False, hosts={}, texts=texts, texts_info=tinfo, error="STIG Manager could not be read: %s" % redact(str(e)))
    module.exit_json(changed=False, hosts=hosts, texts=texts, texts_info=tinfo, error="")


if __name__ == "__main__":
    run_main(main)     # 1.77.0h: an unexpected error redacted, no raw traceback
