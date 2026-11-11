#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write the reviews an approval allowed to STIG Manager (and create the new assets it allowed) - nothing else. The plan comes from the STIG recheck's check
(plugins/filter/stig_filters.py, stig_update_plan) through the workflow's approval step. Each review is read again first
and written only if it is still as the check read it; then read once more, to say what STIG Manager now holds. Two
requests to STIG Manager only: GET /collections/{c}/reviews/{asset}/{rule} and POST /collections/{c}/reviews/{asset}
(the reviews of the plan). A new asset the approval allowed (0.72.0, check_stig_update_create_assets; workflow 40's checklist
import) is created first - GET /assets?collectionId= to see it is still not there, then POST /assets with its STIGs - and
its reviews written. No
collection, user, STIG or grant, and no asset that is there, is ever changed. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stigman_update
short_description: Write the approved reviews to STIG Manager, each only if unchanged since it was read
description:
  - Gets a token from C(token_url) with the client credentials grant (a client with the scope stig-manager:collection
    and a grant in the collections that lets it write reviews).
  - For each review of C(plan) - grouped by collection and asset - reads the review again; one that changed since the
    check read it (its result, status or time) is left as it is (state C(changed since)).
  - Writes the rest of an asset's reviews in one POST /collections/{collectionId}/reviews/{assetId} - result, detail,
    comment (null keeps the review's own), resultEngine, status - and reads each again after.
  - A review with attachments in STIG Manager (its metadata key C(artifacts): the files a person attached as evidence)
    is left as it is (state C(has attachments)) - unless C(skip_attachments) is false. Read again just before the
    write, so one attached after the check is left too.
  - A plan item C(kind=create) (a host with no asset) - its collection's assets read again: one of the host's names (the
    asset's name or FQDN, or their short names) there by now - created by a person since the check - is left, and
    its reviews are not written (state C(there since the check), which fails the job); one with the plan's name and the
    description this job gives it was made by an earlier run of the same plan (state C(there from an earlier run)): its
    reviews are written to it, each read again first as any asset's; else POST /assets (name, FQDN, the STIG; the Manage
    grant) and the reviews of the plan with C(new_asset) its name are written to it (none to read again: it is new),
    then read back in one GET.
  - The checklist import's items (0.76.0: it may look in several collections) - the client's grants read (GET /user): a
    review is written only where it has Full or more (state C(no grant) where it has Restricted or none), a new asset
    created only where it has Manage or more.
  - In check mode reads, and writes nothing (state C(would write), C(would create)).
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client that may write reviews., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  plan:
    description: "[{collectionId, assetId, ruleId, was: {result, status, ts}, result, status, detail, comment, engine, line, ...}]
      - stig_update_plan's items, as the approval step handed them on; a new asset {kind: create, collectionId, asset, fqdn,
      benchmark, line}, and its reviews with assetId '' and new_asset: its name. The checklist import's (workflow 40,
      site_ckl_import): reviews of kind import (engine null: a checklist a person answered), a new asset with benchmarks
      (every STIG its checklists matched), description, ip, mac."
    type: list
    elements: dict
    required: true
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
  skip_attachments: {description: Leave a review that has attachments as it is., type: bool, default: true}
author: site automation
'''

RETURN = r'''
items:
  description: "Each review of the plan with its state - written, would write, changed since, has attachments, rejected (STIG Manager's reason),
                not as written (read back otherwise), bad (not a plan item), error, asset not created, no grant (the import's: the
                client has less than Full there) - and why, and what STIG Manager holds now; each new asset (kind create): created,
                would create, there since the check, there from an earlier run, bad, error, no grant - and its assetId."
  type: list
  returned: always
error:
  description: Why nothing could be done (the token), in plain words ('' = done).
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

ID_RE = re.compile(r"^[0-9]{1,20}$")
# a rule id: any text up to 45 characters (STIG Manager's own rule) - DISA's SV-257985r1069364_rule, and SV-PHTN-50-000003 as STIG
# content from elsewhere has; no control characters, no / (it is a part of the request's path)
RULE_RE = re.compile(r"^[^\x00-\x1f\x7f/]{1,45}$")
RESULTS = ("pass", "fail", "notapplicable")
STATUSES = ("saved", "submitted", "accepted")
ENGINES = ("scap", "script", "other")
# a STIG's benchmark id: any text up to 255 characters (STIG Manager's own rule) - DISA's RHEL_9_STIG, and ones with spaces as
# STIG content from elsewhere has (VMware Aria Operations 8.x Apache); no control characters
BENCH_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,255}$")
# a collection grant's role (GET /user, collectionGrants[].roleId)
ROLES = {1: "Restricted", 2: "Full", 3: "Manage", 4: "Owner"}


class NotFound(RuntimeError):
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
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 404:
                raise NotFound("HTTP 404 from %s: %s" % (req.get_full_url().split("?")[0], body.strip() or e.reason))
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

    def review(self, c, a, rule):
        """The review of an asset and rule as STIG Manager holds it now (None: no review)."""
        req = Request("%s/collections/%s/reviews/%s/%s" % (self.api, quote(c, safe=''), quote(a, safe=''), quote(rule, safe='')),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return self._open(req)

    def attachments(self, c, a, rule):
        """(how many attachments the review has, their names) - STIG Manager keeps them in the review's metadata, the key
        'artifacts' (a JSON text of their list); 0 without the key, or without a review. -1: the key is there, its value
        not such a list."""
        base = "%s/collections/%s/reviews/%s/%s/metadata/keys" % (self.api, quote(c, safe=''), quote(a, safe=''), quote(rule, safe=''))
        hdr = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        try:
            keys = self._open(Request(base, headers=hdr)) or []
            if "artifacts" not in keys:
                return 0, []
            v = self._open(Request(base + "/artifacts", headers=hdr))
        except NotFound:
            return 0, []
        for _ in range(2):
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    return (-1, []) if v.strip() not in ("", "[]") else (0, [])
        if isinstance(v, list):
            return len(v), [str((x or {}).get("name") or "") for x in v if isinstance(x, dict)]
        return (0, []) if v in (None, "", {}) else (-1, [])

    def _send(self, method, path, body):
        req = Request(self.api + path, data=json.dumps(body).encode("utf-8"),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "Content-Type": "application/json"})
        req.get_method = lambda: method
        return self._open(req)

    def grants(self):
        """{collectionId: roleId} of this client's grants (GET /user)."""
        req = Request("%s/user" % self.api, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        u = self._open(req) or {}
        return dict((str((g.get("collection") or {}).get("collectionId")), int(g.get("roleId") or 0)) for g in u.get("collectionGrants") or [])

    def assets(self, c):
        """The collection's assets (name, FQDN, STIGs)."""
        req = Request("%s/assets?%s" % (self.api, urlencode([("collectionId", c), ("projection", "stigs")])),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return self._open(req) or []

    def create(self, c, name, fqdn, benches, description="", ip="", mac=""):
        """POST /assets: the new asset with its STIGs (the collection's revision) -> STIG Manager's record of it."""
        return self._send("POST", "/assets?projection=stigs", {
            "name": name, "fqdn": fqdn or None, "collectionId": c,
            "description": description or RECHECK_DESCRIPTION,
            "ip": ip or "", "mac": mac or "", "noncomputing": False, "metadata": {}, "stigs": list(benches)}) or {}

    def asset_reviews(self, c, a):
        """Every review of an asset: {ruleId: what STIG Manager holds}."""
        req = Request("%s/collections/%s/reviews/%s" % (self.api, quote(c, safe=''), quote(a, safe='')),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        return dict((r.get("ruleId"), held(r)) for r in self._open(req) or [])

    def write(self, c, a, reviews):
        """POST /collections/{c}/reviews/{a}: the asset's reviews of the plan -> {affected, rejected}."""
        req = Request("%s/collections/%s/reviews/%s" % (self.api, quote(c, safe=''), quote(a, safe='')), data=json.dumps(reviews).encode("utf-8"),
                      headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "Content-Type": "application/json"})
        req.get_method = lambda: "POST"
        return self._open(req) or {}


def short(name):
    return (name or "").lower().split(".")[0]


RECHECK_DESCRIPTION = "Created by the STIG recheck (site-automation), approved in AAP"


def made_by_this_plan(it, asset):
    """Is this asset the one this plan item creates - made by an earlier run of the same approved plan (one whose
    reviews were then not all written)? The same name in the plan's collection and the description this job gives it."""
    want = str(it.get("description") or "")[:255] or RECHECK_DESCRIPTION
    return (str(asset.get("name") or "").lower() == str(it.get("asset") or "").lower()
            and str(asset.get("description") or "") == want and bool(ID_RE.match(str(asset.get("assetId") or ""))))


def matching(names, assets):
    """The assets whose name or FQDN is one of names, or has its short name (as the check's read looks them up)."""
    want = set(n.lower() for n in names if n)
    shorts = set(short(n) for n in want)
    return [a for a in assets if (a.get("name") or "").lower() in want or (a.get("fqdn") or "").lower() in want
            or short(a.get("name")) in shorts or (a.get("fqdn") and short(a.get("fqdn")) in shorts)]


def bad_create(it):
    """Why a new asset of the plan cannot be created ('' = it can)."""
    if not ID_RE.match(str(it.get("collectionId", ""))):
        return "no collection id"
    name = str(it.get("asset") or "")
    if not name.strip() or len(name) > 255:
        return "no asset name"
    if not all(BENCH_RE.match(str(b or "")) for b in benches_of(it)):
        return "no STIG (benchmark)"
    return ""


def benches_of(it):
    """A new asset's STIGs: benchmarks (the checklist import: every STIG its checklists matched), else its one benchmark."""
    return list(it.get("benchmarks") or []) or [it.get("benchmark")]


def no_grant(grants, it, need):
    """Why the client may not do this in the item's collection ('' = it may): the checklist import's items only, and only
    when the grants were read."""
    if grants is None or not (it.get("kind") == "import" or it.get("import")):
        return ""
    has = grants.get(str(it.get("collectionId", "")), 0)
    if has >= need:
        return ""
    return "the client has %s in %s - %s needs %s or more%s" % (
        ROLES.get(has, "no grant"), it.get("collection") or "the collection " + str(it.get("collectionId", "")),
        "creating an asset" if need >= 3 else "the import writes only where it", ROLES[need],
        " (a Restricted grant reaches only the assets given to it)" if has == 1 else "")


def make_assets(p, client, check, grants=None):
    """The plan's new assets -> (their rows, {(collectionId, name lower): assetId, 'check', or None - not created})."""
    rows, ids = [], {}
    for n, it in enumerate(p["plan"]):
        if it.get("kind") != "create":
            continue
        c, name = str(it.get("collectionId", "")), str(it.get("asset") or "")
        row = {"n": n, "kind": "create", "line": it.get("line", ""), "host": it.get("host", ""), "asset": name, "fqdn": it.get("fqdn") or "",
               "collection": it.get("collection", ""), "benchmark": ", ".join(str(x) for x in benches_of(it) if x), "revision": it.get("revision", ""),
               "reviews": it.get("reviews", 0), "assetId": ""}
        rows.append(row)
        key = (c, name.lower())
        if key in ids:              # the plan has it twice (two hosts, one name): the check never plans that
            row.update(state="bad", why="the plan has this asset twice - not created again")
            continue
        ids[key] = None
        why = bad_create(it)
        if why:
            row.update(state="bad", why=why)
            continue
        why = no_grant(grants, it, 3)
        if why:
            row.update(state="no grant", why=why + " - not created")
            continue
        try:
            there = matching([name, it.get("fqdn") or ""], client.assets(c))
        except RuntimeError as e:
            row.update(state="error", why="the collection's assets could not be read - not created: %s" % e)
            continue
        if len(there) == 1 and made_by_this_plan(it, there[0]):
            # made by an earlier run of this plan: its reviews are written to it, each read again first (as any asset's)
            a = str(there[0]["assetId"])
            row.update(state="there from an earlier run", assetId=a, collectionId=c,
                       why="created by an earlier run of this plan - its reviews written to it, each read again first")
            ids[key] = a
            continue
        if there:
            row.update(state="there since the check", assetId=str(there[0].get("assetId") or ""),
                       why="%s is in %s now (made since the check) - not created, and its reviews not written: run the %s again" % (
                           there[0].get("name"), it.get("collection") or "the collection", "import" if it.get("benchmarks") else "recheck"))
            continue
        if check:
            row.update(state="would create", why="")
            ids[key] = "check"
            continue
        try:
            ans = client.create(c, name, it.get("fqdn") or "", benches_of(it), str(it.get("description") or "")[:255],
                                str(it.get("ip") or "")[:255], str(it.get("mac") or "")[:255])
        except RuntimeError as e:
            row.update(state="error", why="not created: %s" % e)
            continue
        a = str(ans.get("assetId") or "")
        if not ID_RE.match(a):
            row.update(state="error", why="STIG Manager gave no asset id")
            continue
        row["assetId"] = a
        got = dict((x.get("benchmarkId"), x.get("revisionStr") or "") for x in ans.get("stigs") or [])
        missing = [b for b in benches_of(it) if b not in got]
        if len(missing) == len(benches_of(it)):
            # created, without the STIG: its reviews could not be written - say so, and leave the asset for a person
            row.update(state="error", why="created (asset id %s), but without %s - its reviews not written: assign the STIG in STIG Manager "
                                          "and run the %s again" % (a, ", ".join(missing), "import" if it.get("benchmarks") else "recheck"))
            continue
        read = dict(zip(benches_of(it), [x.strip() for x in str(it.get("revision") or "").split(",")]))
        notes = ["with %s %s (the check read %s)" % (b, got[b], read[b]) for b in benches_of(it) if b in got and read.get(b) and got[b] != read[b]]
        if missing:     # STIG Manager rejects the reviews of those (said for each)
            notes.append("without %s - assign it in STIG Manager" % ", ".join(missing))
        row.update(state="created", why="; ".join(notes))
        ids[key] = a
    return rows, ids


def label_of(review):
    st = (review or {}).get("status") or {}
    return st.get("label", "") if isinstance(st, dict) else str(st)


def held(review):
    """What STIG Manager holds now, in the plan's words: {result, status, ts}."""
    if not review:
        return {"result": "", "status": "", "ts": ""}
    return {"result": review.get("result") or "", "status": label_of(review), "ts": review.get("ts") or review.get("touchTs") or ""}


def same(was, now):
    """Is the review as the check read it? No review then: none now (or one of Not Reviewed with nothing in it)."""
    if not was.get("result"):
        return not now["result"] or (now["result"] == "notchecked" and (not was.get("ts") or now["ts"] == was.get("ts")))
    return now["result"] == was["result"] and now["status"] == was.get("status", "") and (not was.get("ts") or now["ts"] == was["ts"])


def bad_item(it):
    """Why a plan item cannot be written ('' = it can): only the ids, results and statuses the plan gives."""
    if not ID_RE.match(str(it.get("collectionId", ""))) or not ID_RE.match(str(it.get("assetId", ""))):
        return "no collection or asset id"
    if not RULE_RE.match(str(it.get("ruleId", ""))) or not str(it.get("ruleId", "")).strip("."):   # "..": a step up the path
        return "not a STIG rule id: %s" % it.get("ruleId")
    if it.get("result") not in RESULTS:
        return "a result this job never writes: %s" % it.get("result")
    if it.get("status") not in STATUSES:
        return "a status this job never writes: %s" % it.get("status")
    if not isinstance(it.get("was"), dict):
        return "no record of the review as it was read"
    if not str(it.get("detail") or "").strip():
        return "no detail"
    # a checklist a person answered (STIG Viewer) has no result engine: the checklist import's reviews only
    if ((it.get("engine") or {}).get("type")) not in ENGINES and not (it.get("kind") == "import" and it.get("engine") is None):
        return "no result engine"
    return ""


def body_of(it):
    eng = dict((k, v) for k, v in (it.get("engine") or {}).items() if k in ("type", "product", "version", "time") and v)
    out = {"ruleId": it["ruleId"], "result": it["result"], "detail": str(it["detail"])[:32767],
           "comment": None if it.get("comment") is None else str(it["comment"])[:32767], "resultEngine": eng or None, "status": it["status"]}
    return out


def update(p, client, check):
    grants = None
    if any(it.get("kind") == "import" or it.get("import") for it in p["plan"]):
        try:
            grants = client.grants()
        except RuntimeError:
            grants = None               # not read: STIG Manager itself refuses what the grant does not allow
    items, ids = make_assets(p, client, check, grants)
    again = set((r.get("collectionId"), r["asset"].lower()) for r in items if r.get("state") == "there from an earlier run")
    groups, fresh = {}, {}
    for n, it in enumerate(p["plan"]):
        if it.get("kind") == "create":
            continue
        row = {"n": n, "line": it.get("line", ""), "host": it.get("host", ""), "asset": it.get("asset", ""), "ruleId": it.get("ruleId", ""),
               "v": it.get("v", ""), "result": it.get("result", ""), "status": it.get("status", ""), "was": it.get("was") or {}}
        if it.get("new_asset"):
            row["new_asset"] = it["new_asset"]
            a = ids.get((str(it.get("collectionId", "")), str(it["new_asset"]).lower()))
            if not a:
                row.update(state="asset not created", why="its new asset %s was not created - not written" % it["new_asset"])
                items.append(row)
                continue
            if a == "check":
                why = bad_item(dict(it, assetId="0"))
                row.update(state="bad" if why else "would write", why=why)
                items.append(row)
                continue
            it = dict(it, assetId=a)
        why = bad_item(it)
        if why:
            row.update(state="bad", why=why)
            items.append(row)
            continue
        why = no_grant(grants, it, 2)
        if why:
            row.update(state="no grant", why=why)
            items.append(row)
            continue
        items.append(row)
        if it.get("new_asset") and (str(it["collectionId"]), str(it["new_asset"]).lower()) not in again:
            fresh.setdefault((str(it["collectionId"]), str(it["assetId"])), []).append((row, it))
        else:
            groups.setdefault((str(it["collectionId"]), str(it["assetId"])), []).append((row, it))
    for (c, a), rows in sorted(fresh.items()):
        write_new(client, c, a, rows)
    for (c, a), rows in sorted(groups.items()):
        go = []
        for row, it in rows:
            try:
                now = held(client.review(c, a, it["ruleId"]))
            except RuntimeError as e:
                row.update(state="error", why=str(e))
                continue
            row["before"] = now
            if now["result"] == it["result"] and now["status"] == it["status"]:
                row.update(state="already so", why="")          # written before (this plan run twice), or by a person
                continue
            if not same(it["was"], now):
                row.update(state="changed since", why="STIG Manager has it as %s (%s, %s) now, not as the check read it - left as it is" % (
                    now["result"] or "no review", now["status"] or "-", now["ts"] or "-"))
                continue
            if p.get("skip_attachments", True):
                try:
                    n, names = client.attachments(c, a, it["ruleId"])
                except RuntimeError as e:
                    row.update(state="error", why="its attachments could not be read - not written: %s" % e)
                    continue
                if n:
                    row.update(state="has attachments", why="%s in STIG Manager%s - left as it is (the evidence stays with its result)" % (
                        ("%d attachment(s)" % n) if n > 0 else "attachments", (": " + ", ".join(names[:5])) if names else ""))
                    continue
            go.append((row, it))
        if not go:
            continue
        if check:
            for row, it in go:
                row.update(state="would write", why="")
            continue
        try:
            ans = client.write(c, a, [body_of(it) for _, it in go])
        except RuntimeError as e:
            for row, it in go:
                row.update(state="error", why=str(e))
            continue
        refused = dict((r.get("ruleId"), r.get("reason") or "rejected") for r in ans.get("rejected") or [])
        for row, it in go:
            if it["ruleId"] in refused:
                row.update(state="rejected", why="STIG Manager: " + refused[it["ruleId"]])
                continue
            try:
                after = held(client.review(c, a, it["ruleId"]))
            except RuntimeError as e:
                row.update(state="error", why="written, but not read back: %s" % e)
                continue
            row["after"] = after
            if after["result"] == it["result"] and after["status"] == it["status"]:
                row.update(state="written", why="")
            else:
                row.update(state="not as written", why="STIG Manager holds %s (%s)" % (after["result"] or "no review", after["status"] or "-"))
    return items


def write_new(client, c, a, rows):
    """A new asset's reviews: nothing to read first (it was created just now, with none), one POST, read back in one GET."""
    try:
        ans = client.write(c, a, [body_of(it) for _, it in rows])
    except RuntimeError as e:
        for row, it in rows:
            row.update(state="error", why=str(e))
        return
    refused = dict((r.get("ruleId"), r.get("reason") or "rejected") for r in ans.get("rejected") or [])
    try:
        now = client.asset_reviews(c, a)
    except RuntimeError as e:
        now, err = None, str(e)
    for row, it in rows:
        row["before"] = {"result": "", "status": "", "ts": ""}
        if it["ruleId"] in refused:
            row.update(state="rejected", why="STIG Manager: " + refused[it["ruleId"]])
        elif now is None:
            row.update(state="error", why="written, but not read back: %s" % err)
        else:
            after = now.get(it["ruleId"]) or held(None)
            row["after"] = after
            if after["result"] == it["result"] and after["status"] == it["status"]:
                row.update(state="written", why="")
            else:
                row.update(state="not as written", why="STIG Manager holds %s (%s)" % (after["result"] or "no review", after["status"] or "-"))


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        plan=dict(type="list", elements="dict", required=True),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
        skip_attachments=dict(type="bool", default=True),
    ), supports_check_mode=True)
    p = module.params
    client = Client(p)
    try:
        client.login()
    except RuntimeError as e:
        module.exit_json(changed=False, items=[], error="no token for the client %s: %s" % (p["client_id"], e))
    items = update(p, client, module.check_mode)
    module.exit_json(changed=any(i.get("state") in ("written", "created") for i in items), items=items, error="")


if __name__ == "__main__":
    main()
