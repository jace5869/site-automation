#!/usr/bin/python
# -*- coding: utf-8 -*-
"""NetApp - expand a volume (1.123.0): list the volumes that may be grown, plan one, grow it - through ONTAP's REST API,
Python's standard library only. The rules and the arithmetic are plugins/module_utils/site_ontap_expand.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ontap_volume
short_description: List, plan and grow NetApp ONTAP volumes (and their LUN) for "NetApp - expand a volume"
description:
  - action=list reads every cluster (GET only) - its volumes, aggregates, LUNs and data addresses - and returns the volumes
    that may be grown, one per LUN when a volume holds several, and the ones that may not, each with its reason.
  - action=plan reads one volume live (GET only) and says what a run would do; action=grow does it - the volume, then its LUN
    by the same size (ontap_expand_lun same), each read back. Right before the change the volume and its aggregate are read
    again; anything that moved against the plan stops it. In check mode grow only plans.
  - Never shrinks; one volume per run; an excluded volume is refused even when named by hand; a second run of the same
    choice finds the volume already at its size and does nothing.
  - The account comes from ONTAP_USERNAME and ONTAP_PASSWORD (an AAP "NetApp ONTAP" credential). action=grow needs an ONTAP
    role that may modify volumes and LUNs.
options:
  action: {description: list, plan or grow., type: str, required: true, choices: [list, plan, grow]}
  clusters: {description: "The clusters (ontap_clusters): list reads them all; plan and grow only the one the choice names, and only when it is one of these.", type: list, elements: str, required: true}
  choice: {description: The survey's answer (it ends with its key [vol|cluster|svm|volume|lun|bytes])., type: str, default: ''}
  size: {description: The launch's Size box ('' = the default growth)., type: str, default: ''}
  comment_line: {description: Added to the volume's comment with the change ('' = the comment is left as it is)., type: str, default: ''}
  pct: {description: ontap_expand_pct., type: float, default: 10}
  min_gb: {description: ontap_expand_min_gb., type: float, default: 1}
  max_gb: {description: ontap_expand_max_gb., type: float, default: 512}
  max_pct: {description: ontap_expand_aggr_max_pct - the aggregate stays at or under this used., type: float, default: 97}
  exclude: {description: ontap_expand_exclude - name patterns or svm:volume., type: list, elements: str, default: []}
  lun_mode: {description: ontap_expand_lun - same (the LUN grows by the same size as the volume)., type: str, default: same, choices: [same]}
  wait_seconds: {description: How long to wait for ONTAP's job of a change., type: int, default: 300}
  expect_volume_uuid: {description: "grow: the volume the plan read (its UUID) - another volume of the same name is refused.", type: str, default: ''}
  expect_lun_uuid: {description: "grow: the LUN the plan read (its UUID).", type: str, default: ''}
  max_vol_growth: {description: "grow: the volume's growth the plan said (bytes) - never more (-1 = no limit).", type: int, default: -1}
  validate_certs: {description: Check the clusters' certificates., type: bool, default: true}
  ca_path: {description: A CA file to check them with., type: str, default: ''}
  timeout: {description: Seconds for one request., type: int, default: 30}
  tls_legacy: {description: ontap_tls_legacy., type: bool, default: false}
author: site automation
'''

RETURN = r'''
clusters: {description: "list: each cluster read - {address, name, reachable, error}.", type: list, returned: always}
choices: {description: "list: the volumes that may be grown (site_ontap_expand.choice_text's fields + choice, naa, nfs_path, nfs_ips).", type: list, returned: always}
excluded: {description: "list: [{volume, svm, cluster, why, address, size, used, aggr, aggr_free, uuid, root}].", type: list, returned: always}
aggregates: {description: "list: every aggregate read - [{cluster, address, name, node, size, used}].", type: list, returned: always}
plan: {description: "plan, grow: site_ontap_expand.plan's answer + volume_uuid, lun_uuid, naa, nfs_path, nfs_ips, cluster.", type: dict, returned: always}
steps: {description: "grow: [{step, exactly, ok, result}] in order.", type: list, returned: always}
'''

import base64
import json
from http.client import HTTPException
import os
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import parse_qsl, urlencode
from ansible.module_utils.six.moves.urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler
from ansible.module_utils.site_ontap import Unreachable, error_text, explain, kind_of, ssl_context
from ansible.module_utils import site_ontap_expand as X

VOL_FIELDS = ("uuid,name,svm.name,aggregates.name,state,type,style,is_svm_root,space.size,space.used,space.available,"
              "space.snapshot.reserve_percent,space.snapshot.used,guarantee.type,autosize.mode,autosize.maximum,nas.path,comment")
AGGR_FIELDS = "uuid,name,space.block_storage.size,space.block_storage.used"
LIST_AGGR_FIELDS = AGGR_FIELDS + ",node.name"     # the list's aggregates also name their node (the portal's NetApp page)
# space.used: the blocks written (drops only after UNMAP); space.guarantee.requested: space-reserved (thick) or thin
LUN_FIELDS = "uuid,name,svm.name,location.volume.name,space.size,space.used,space.guarantee.requested,serial_number"


class Failed(Exception):
    """ONTAP answered, but not with what was asked: said as it is"""


class NoRedirect(HTTPRedirectHandler):
    """A cluster that answers with a redirect is not followed: the account would go to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Failed("the cluster answered with a redirect (HTTP %s to %s): give its management address itself in ontap_clusters"
                     % (code, str(newurl).split("?")[0][:120]))


class Client(object):
    """One cluster. GET for reading; PATCH only from grow()."""

    def __init__(self, address, user, password, context, timeout, opener=None):
        self.address, self.timeout = address, timeout
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
        self.opener = opener or build_opener(HTTPSHandler(context=context), NoRedirect())

    def call(self, method, path, query=None, body=None):
        url = (self.address if "://" in self.address else "https://" + self.address).rstrip("/") + path
        if query:
            url += "?" + urlencode(query, safe="*|,.:=")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(url, data=data, headers={"Authorization": self.auth, "Accept": "application/json", "Content-Type": "application/json"})
        req.get_method = lambda: method
        tries = 0
        while True:
            try:
                r = self.opener.open(req, timeout=self.timeout)
                code, text = r.getcode(), r.read().decode("utf-8", "replace")
            except HTTPError as e:
                code, text = e.code, e.read().decode("utf-8", "replace")
            except (URLError, ssl.SSLError, OSError, HTTPException) as e:
                kind = kind_of(e)
                if kind == "timeout" and method == "GET" and tries < 1:      # a change is never sent twice
                    tries += 1
                    continue
                raise Unreachable(str(getattr(e, "reason", e)) or e.__class__.__name__, kind)
            if code == 401:
                raise Unreachable("the ONTAP account was refused (HTTP 401): check the NetApp ONTAP credential", "auth")
            if code >= 300:
                raise Failed(error_text(code, text) + (" - the account's ONTAP role may not allow this" if code == 403 else ""))
            try:
                return json.loads(text or "{}")
            except ValueError:
                raise Failed("not JSON: " + (text or "")[:200])

    def records(self, path, fields, **query):
        out, q, pages = [], dict(query, fields=fields, max_records="1000"), 0
        data = self.call("GET", path, q)
        while True:
            if data.get("errors") or data.get("error"):    # a partial answer (some nodes did not answer): never taken as the whole list
                e = (data.get("errors") or [data.get("error")])[0] or {}
                raise Failed("ONTAP answered %s only in part: %s" % (path, e.get("message") or e))
            out.extend(data.get("records") or [])
            nxt = ((data.get("_links") or {}).get("next") or {}).get("href")
            pages += 1
            if not nxt:                             # an empty page with a next link is followed too (a timed collection)
                return out
            if pages >= 1000:
                raise Failed("%s has more than 1000 pages: not read whole" % path)
            p, _, qs = nxt.partition("?")
            data = self.call("GET", p, dict(parse_qsl(qs, keep_blank_values=True)))

    def wait(self, answer, seconds):
        """A change answered 202 with a job: wait for it -> '' (done) or why not"""
        job = (answer or {}).get("job") or {}
        if not job.get("uuid"):
            return ""
        end = time.time() + seconds
        while True:
            j = self.call("GET", "/api/cluster/jobs/" + job["uuid"], {"fields": "state,message,code"})
            st = j.get("state")
            if st == "success":
                return ""
            if st in ("failure", "error"):
                return "ONTAP's job failed: %s" % (j.get("message") or j.get("code") or st)
            if time.time() > end:
                return "ONTAP's job %s did not finish within %d s (it may still finish: read the volume again)" % (job["uuid"], seconds)
            time.sleep(1)


def gb_of(rec, *path):
    v = rec
    for k in path:
        v = (v or {}).get(k)
    return int(v or 0)


def vol_in(rec):
    return {"name": rec.get("name"), "svm": (rec.get("svm") or {}).get("name") or "", "size": gb_of(rec, "space", "size"),
            "uuid": rec.get("uuid"),
            "used": gb_of(rec, "space", "used"), "state": rec.get("state"), "type": rec.get("type"), "style": rec.get("style"),
            "available": None if (rec.get("space") or {}).get("available") is None else gb_of(rec, "space", "available"),
            "snap_pct": float(((rec.get("space") or {}).get("snapshot") or {}).get("reserve_percent") or 0),
            "snap_used": int(((rec.get("space") or {}).get("snapshot") or {}).get("used") or 0),
            "guarantee": (rec.get("guarantee") or {}).get("type") or "",
            "autosize": (rec.get("autosize") or {}).get("mode") or "", "autosize_max": int((rec.get("autosize") or {}).get("maximum") or 0),
            "is_svm_root": rec.get("is_svm_root"), "aggregates": [a.get("name") for a in rec.get("aggregates") or []]}


def aggr_in(rec):
    bs = (rec.get("space") or {}).get("block_storage") or {}
    return {"name": rec.get("name"), "size": int(bs.get("size") or 0), "used": None if bs.get("used") is None else int(bs["used"])}


def lun_thick(rec):
    """a LUN is space-reserved (thick) when ONTAP says its space guarantee is requested"""
    g = ((rec.get("space") or {}).get("guarantee") or {}).get("requested")
    return g is True or str(g).lower() == "true"


def ips(c):
    """each SVM's data addresses (an NFS datastore names one of them)"""
    out = {}
    for r in c.records("/api/network/ip/interfaces", "svm.name,ip.address", scope="svm"):
        out.setdefault((r.get("svm") or {}).get("name") or "", []).append((r.get("ip") or {}).get("address") or "")
    return out


def cluster_name(c):
    try:
        return c.call("GET", "/api/cluster", {"fields": "name"}).get("name") or c.address
    except Failed:
        return c.address


def list_cluster(c, opts):
    name = cluster_name(c)
    vols = c.records("/api/storage/volumes", VOL_FIELDS)
    arecs = c.records("/api/storage/aggregates", LIST_AGGR_FIELDS)
    aggrs = {a["name"]: aggr_in(a) for a in arecs}
    luns = {}
    for l_ in c.records("/api/storage/luns", LUN_FIELDS):
        luns.setdefault(((l_.get("svm") or {}).get("name"), ((l_.get("location") or {}).get("volume") or {}).get("name")), []).append(l_)
    addr = ips(c)
    choices, excluded = [], []
    for v in sorted(vols, key=lambda r: ((r.get("svm") or {}).get("name") or "", r.get("name") or "")):
        why = X.excluded(v, opts["exclude"])
        svm = (v.get("svm") or {}).get("name") or ""
        vi = vol_in(v)
        a = aggrs.get((vi["aggregates"] or [""])[0]) or {"name": (vi["aggregates"] or ["?"])[0], "size": 0, "used": 0}
        if why:
            # the sizes too (the portal's NetApp page shows a volume not offered with its reason)
            excluded.append({"volume": v.get("name"), "svm": svm or "(node)", "cluster": name, "why": why, "address": c.address,
                             "size": vi["size"], "used": vi["used"], "aggr": a["name"], "aggr_free": max(0, a["size"] - (a["used"] or 0)),
                             "uuid": v.get("uuid") or "", "root": bool(v.get("is_svm_root")) or v.get("name") == "vol0" or not svm})
            continue
        base = {"cluster": name, "address": c.address, "svm": svm, "volume": vi["name"], "size": vi["size"], "used": vi["used"],
                "aggr": a["name"], "aggr_free": max(0, a["size"] - (a["used"] or 0)), "datastore": "", "nfs_path": (v.get("nas") or {}).get("path") or "",
                "nfs_ips": addr.get(svm, []), "lun": "", "luns": 0, "naa": ""}
        mine = sorted(luns.get((svm, vi["name"]), []), key=lambda x: x.get("name") or "")
        for l_ in mine or [None]:
            e = dict(base)
            if l_:
                e.update(lun=l_.get("name") or "", luns=len(mine), naa=X.naa(l_.get("serial_number")), lun_size=gb_of(l_, "space", "size"),
                         lun_uuid=l_.get("uuid") or "", lun_used=gb_of(l_, "space", "used"), lun_thick=lun_thick(l_),
                         vol_available=vi["available"], snap_pct=vi["snap_pct"], guarantee=vi["guarantee"])
            e["uuid"] = v.get("uuid") or ""
            choices.append(e)
    ag_out = [{"cluster": name, "address": c.address, "name": r.get("name") or "", "node": (r.get("node") or {}).get("name") or "",
               "size": aggr_in(r)["size"], "used": aggr_in(r)["used"]} for r in arecs]
    return name, choices, excluded, ag_out


def read_one(c, key, opts, size_text):
    """the volume of a choice, live -> (plan, ids)"""
    vols = [v for v in c.records("/api/storage/volumes", VOL_FIELDS, name=key["volume"], **{"svm.name": key["svm"]})
            if v.get("name") == key["volume"] and (v.get("svm") or {}).get("name") == key["svm"]]
    if len(vols) != 1:
        raise Failed("volume %s on SVM %s: %s" % (key["volume"], key["svm"], "not found on this cluster" if not vols else "found %d times" % len(vols)))
    v = vols[0]
    if not key.get("uuid") or v.get("uuid") != key["uuid"]:
        raise Failed("volume %s on SVM %s is not the one the list showed (it was made again since): run NetApp - refresh volume list and "
                     "pick it again" % (key["volume"], key["svm"]))
    vi = vol_in(v)
    an = (vi["aggregates"] or [""])[0]
    ag = [a for a in c.records("/api/storage/aggregates", AGGR_FIELDS, name=an) if a.get("name") == an] if an else []
    aggr = aggr_in(ag[0]) if ag else {"name": an or "?", "size": 0, "used": None}
    lun, lrec, count = None, None, 0
    luns = c.records("/api/storage/luns", LUN_FIELDS, **{"svm.name": key["svm"], "location.volume.name": key["volume"]})
    luns = [x for x in luns if ((x.get("location") or {}).get("volume") or {}).get("name") == key["volume"]]
    count = len(luns)
    if key["lun"]:
        hit = [x for x in luns if x.get("name") == key["lun"]]
        if not hit:
            raise Failed("LUN %s is not in volume %s any more - run NetApp - refresh volume list and pick it again" % (key["lun"], key["volume"]))
        lrec = hit[0]
        if lrec.get("uuid") != key.get("lun_uuid"):
            raise Failed("LUN %s is not the one the list showed (it was made again since): run NetApp - refresh volume list and pick it "
                         "again" % key["lun"])
        lun = {"name": lrec["name"], "size": gb_of(lrec, "space", "size"), "used": gb_of(lrec, "space", "used"), "thick": lun_thick(lrec),
               "total": sum(gb_of(x, "space", "size") for x in luns)}
    elif count and not X.excluded(v, opts["exclude"]):
        raise Failed("volume %s holds %d LUN(s): pick its LUN in the list (each LUN is its own choice), so the LUN grows with it"
                     % (key["volume"], count))
    p = X.plan(vi, aggr, key["listed"], opts, lun=lun, size_text=size_text, lun_listed=key.get("lun_listed") or 0)
    nfs_path = (v.get("nas") or {}).get("path") or ""
    p.update(volume_uuid=v.get("uuid"), lun_uuid=(lrec or {}).get("uuid") or "", naa=X.naa((lrec or {}).get("serial_number")),
             nfs_path=nfs_path, nfs_ips=ips(c).get(key["svm"], []) if nfs_path and not lrec else [], luns=count,
             comment=v.get("comment") or "", aggregate_uuid=(ag[0].get("uuid") if ag else ""), autosize=vi["autosize"])
    return p, v


def grow(c, p, key, opts, line, wait):
    """the change, step by step -> steps; stops at the first that fails. A lost connection after the volume changed is a
    failed step of its own, so the report still says what was changed."""
    steps = []
    vols = [v for v in c.records("/api/storage/volumes", VOL_FIELDS, uuid=p["volume_uuid"]) if v.get("uuid") == p["volume_uuid"]]
    now = vol_in(vols[0]) if vols else None
    an = (now or {}).get("aggregates") and now["aggregates"][0]
    ag = [a for a in c.records("/api/storage/aggregates", AGGR_FIELDS, name=an) if a.get("name") == an] if an else []
    if now is None:
        why = "the volume is gone since the plan - nothing was changed"
    elif now["name"] != key["volume"] or now["svm"] != key["svm"]:
        why = "the volume is now %s on %s - nothing was changed" % (now["name"], now["svm"])
    else:
        why = X.recheck(p, now, aggr_in(ag[0]) if ag else {"name": an, "size": 0, "used": None}, opts["exclude"])
    steps.append({"step": "Read the volume and %s again" % p["aggr"], "exactly": "right before the change", "ok": not why,
                  "result": why or "unchanged since the plan"})
    if why:
        return steps
    if p.get("lun"):
        # the volume's growth again from what is there now (free space, snapshots, the LUNs): a snapshot deleted since the
        # plan may have made the room - never more than the plan's, and the aggregate asked again for it
        luns = [x for x in c.records("/api/storage/luns", LUN_FIELDS, **{"svm.name": key["svm"], "location.volume.name": key["volume"]})
                if ((x.get("location") or {}).get("volume") or {}).get("name") == key["volume"]]
        mine = [x for x in luns if x.get("uuid") == p["lun_uuid"]]
        if not mine:
            steps[-1].update(ok=False, result="the LUN is gone since the plan - nothing was changed")
            return steps
        lnow = {"name": p["lun"]["name"], "size": gb_of(mine[0], "space", "size"), "thick": lun_thick(mine[0]),
                "total": sum(gb_of(x, "space", "size") for x in luns)}
        if abs(lnow["size"] - p["lun"]["size"]) > X.SLACK:          # before any write: a LUN resized since stops it all
            steps[-1].update(ok=False, result="the LUN is %s now, not %s as planned - nothing was changed; launch it again"
                             % (X.human(lnow["size"]), X.human(p["lun"]["size"])))
            return steps
        vt, how = X.volume_for_lun(now, lnow, p["growth"])
        take = X.landing(now, lnow, vt - now["size"], p["growth"])
        cap = min(p.get("vol_growth", 0), p["vol_growth_cap"]) if p.get("vol_growth_cap", -1) >= 0 else p.get("vol_growth", 0)
        cap = min(cap, X.growth_limit(now, p["growth"]) - now["size"])        # never more than the LUN's growth and its reserve
        if vt - now["size"] > cap + X.SLACK:
            steps[-1].update(ok=False, result="the volume needs more than planned now (+%s, planned +%s) - nothing was changed; launch it again"
                             % (X.human(vt - now["size"]), X.human(cap)))
            return steps
        if ag and not X.aggr_fits(aggr_in(ag[0])["size"], aggr_in(ag[0])["used"], p["max_pct"], take):
            steps[-1].update(ok=False, result="%s has no room for it now - nothing was changed" % p["aggr"])
            return steps
        p.update(vol_growth=vt - now["size"], landing=take, vol_how=how if vt > now["size"] else "it has room for the LUN's growth already (%s)" % how)
    vg = p.get("vol_growth", p["growth"])
    p["target"] = now["size"] + vg                   # what the aggregate was checked for, on the size read just now
    comment = X.comment_with(vols[0].get("comment") or "", line) if line else None       # the comment as it is now, not as planned
    if vg <= 0:                                       # room enough: only the comment (if any) changes
        ok, res = comment_only(c, p, comment, wait, "Not needed - %s" % (p.get("vol_how") or "it has room for the LUN's growth"))
        steps.append({"step": "Grow the volume", "exactly": "size %s stays%s" % (X.human(p["size"]), (", comment + \"%s\"" % line) if line else ""),
                      "ok": ok, "result": res})
        if not ok or not p.get("lun"):
            return steps
        return _grow_lun(c, p, key, wait, steps, p["target"])
    # autosize may grow the volume between the last read and the change, and a size sent then would shrink it: autosize is
    # paused for the change (grow or grow_shrink), the size read once more and sent only when it is bigger, then autosize is
    # put back as it was (its maximum raised to the new size when under it) - also when the change fails
    mode, maxi = now.get("autosize") or "", int(now.get("autosize_max") or 0)
    vname = "%s (%s)" % (key["volume"], key["svm"])

    def size_now():
        """the volume's size read again, or None when it could not be read"""
        try:
            cur = [v for v in c.records("/api/storage/volumes", VOL_FIELDS, uuid=p["volume_uuid"]) if v.get("uuid") == p["volume_uuid"]]
            return vol_in(cur[0])["size"] if cur else None
        except (Failed, Unreachable, IndexError, HTTPException, ValueError):
            return None

    def mode_now():
        """autosize's mode read again, or None when it could not be read"""
        try:
            cur = [v for v in c.records("/api/storage/volumes", VOL_FIELDS, uuid=p["volume_uuid"]) if v.get("uuid") == p["volume_uuid"]]
            return vol_in(cur[0])["autosize"] if cur else None
        except (Failed, Unreachable, IndexError, HTTPException, ValueError):
            return None

    def restore(confirmed):
        """autosize put back as it was; its maximum raised only to a size read back (confirmed) -> the step"""
        new_max = max(maxi, int(confirmed)) if confirmed else maxi
        body = {"autosize": {"mode": mode, "maximum": new_max}} if new_max != maxi else {"autosize": {"mode": mode}}
        try:
            bad = c.wait(c.call("PATCH", "/api/storage/volumes/" + p["volume_uuid"], body=body), wait)
            rok, rres = not bad, bad or ("Done - autosize %s again%s" % (mode, (", its maximum raised %s -> %s (under the volume's size now)"
                                                                               % (X.human(maxi), X.human(new_max))) if new_max != maxi else ""))
        except (Failed, Unreachable, HTTPException, ValueError) as e:
            rok, rres = False, "Failed or not known: %s" % e
        if not rok and mode_now() == mode:          # the answer lost, the change made
            rok, rres = True, "Done (read again: autosize %s)" % mode
        return {"step": "Restore autosize", "exactly": "autosize.mode off -> %s" % mode, "ok": rok,
                "result": rres if rok else "%s - autosize may be OFF: check volume %s in ONTAP System Manager (it was %s)" % (rres, vname, mode)}

    paused = False
    if mode in ("grow", "grow_shrink"):
        try:
            bad = c.wait(c.call("PATCH", "/api/storage/volumes/" + p["volume_uuid"], body={"autosize": {"mode": "off"}}), wait)
            paused, res = not bad, bad or "Done - autosize off for the change"
        except (Failed, Unreachable, HTTPException, ValueError) as e:
            res = "Failed or not known: %s" % e
        if not paused:
            # the answer or the job's poll failed: what is autosize now? off: it was turned off - put it back
            st = mode_now()
            if st == "off":
                steps.append({"step": "Pause autosize (%s)" % mode, "exactly": "autosize.mode %s -> off" % mode, "ok": False,
                              "result": res + " - it went off all the same; the size is not changed, autosize is put back"})
                steps.append(restore(None))
            elif st == mode:
                steps.append({"step": "Pause autosize (%s)" % mode, "exactly": "autosize.mode %s -> off" % mode, "ok": False,
                              "result": res + " - autosize is as it was; nothing was changed"})
            else:
                steps.append({"step": "Pause autosize (%s)" % mode, "exactly": "autosize.mode %s -> off" % mode, "ok": False,
                              "result": "%s - autosize may be OFF (read again: %s): check volume %s in ONTAP System Manager (it was %s); "
                                        "the size is not changed" % (res, st or "not read", vname, mode)})
            return steps
        steps.append({"step": "Pause autosize (%s)" % mode, "exactly": "autosize.mode %s -> off" % mode, "ok": True, "result": res})
    got, ok = None, False
    try:
        cur_size = size_now()
        if cur_size is None:
            steps.append({"step": "Grow the volume", "exactly": "read again before the change", "ok": False,
                          "result": "the volume could not be read again - its size is not sent"})
        elif cur_size >= p["target"] - X.SLACK:        # grown since (autosize before it was paused): never a size under its own
            p["vol_how"] = "it is %s already (grown since the plan, e.g. by autosize) - its size is not sent" % X.human(cur_size)
            p["target"] = got = cur_size
            ok, res = comment_only(c, p, comment, wait, "Not needed - " + p["vol_how"])
            steps.append({"step": "Grow the volume", "exactly": "size %s stays" % X.human(cur_size), "ok": ok, "result": res})
        else:
            # never more than the planned growth on the size there is now (autosize may have shrunk it), never over the planned size
            p["target"] = min(p["target"], cur_size + vg)
            body = {"size": p["target"]}
            if comment is not None:
                body["comment"] = comment
            try:
                bad = c.wait(c.call("PATCH", "/api/storage/volumes/" + p["volume_uuid"], body=body), wait)
            except (Failed, Unreachable, HTTPException, ValueError) as e:
                bad = "Failed or not known: %s" % e
            got = size_now()                           # what it is, whatever the answer said
            ok = not bad and got is not None and got >= p["target"] - X.SLACK
            res = (bad + (" - it reads %s now" % X.human(got) if got is not None else " - read the volume in ONTAP System Manager")) if bad else \
                ("Done - read back %s" % X.human(got) if ok else "ONTAP said done, but it reads %s" % (X.human(got) if got is not None else "nothing"))
            steps.append({"step": "Grow the volume", "exactly": "PATCH /api/storage/volumes/%s size %s -> %s%s%s" % (
                p["volume_uuid"], X.human(cur_size), X.human(p["target"]), (" (%s)" % p["vol_how"]) if p.get("vol_how") else "",
                (", comment + \"%s\"" % line) if line else ""), "ok": ok, "result": res})
    finally:
        if paused:
            r = restore(got if got is not None else None)
            steps.append(r)
            if not r["ok"] and ok and p.get("lun"):
                steps.append({"step": "Grow the LUN", "exactly": p["lun"]["name"], "ok": False,
                              "result": "Not done - autosize could not be put back on the volume: the LUN is left as it was"})
                ok = False
    if not ok or not p.get("lun") or not all(x["ok"] for x in steps):
        return steps
    return _grow_lun(c, p, key, wait, steps, got)


def comment_only(c, p, comment, wait, res):
    """the volume's comment written (when there is one) and nothing else -> (ok, result)"""
    if comment is None:
        return True, res
    try:
        bad = c.wait(c.call("PATCH", "/api/storage/volumes/" + p["volume_uuid"], body={"comment": comment}), wait)
        return not bad, bad or res + "; its comment written"
    except (Failed, Unreachable, HTTPException, ValueError) as e:
        return False, "Failed or not known: %s - the volume's comment; nothing else was changed" % e


def _grow_lun(c, p, key, wait, steps, vol_size=None):
    """the LUN by the growth planned, read again right before (never a size under its own), read back -> steps. vol_size:
    the volume's size as confirmed - the LUN's new size must fit it."""
    t = p["lun"]["target"]
    try:
        cur = [x for x in c.records("/api/storage/luns", LUN_FIELDS, uuid=p["lun_uuid"]) if x.get("uuid") == p["lun_uuid"]]
        size = gb_of(cur[0], "space", "size") if cur else -1
        where = ((cur[0].get("location") or {}).get("volume") or {}).get("name") if cur else ""
        if cur and where != key["volume"]:
            ok, res = False, ("Stopped - the LUN moved to volume %s: the volume is grown, the LUN is not - grow it in ONTAP System Manager"
                              % where)
        elif size <= 0 or abs(size - p["lun"]["size"]) > X.SLACK:
            ok, res = False, ("Stopped - the LUN is %s now, not %s as planned: the volume is grown, grow the LUN in ONTAP System Manager"
                              % (X.human(size) if size >= 0 else "gone", X.human(p["lun"]["size"])))
        elif vol_size is not None and size + p["growth"] > vol_size + X.SLACK:
            ok, res = False, ("Stopped - the LUN's new size %s would be over its volume's %s: grow the volume first, in ONTAP System Manager"
                              % (X.human(size + p["growth"]), X.human(vol_size)))
        else:
            t = p["lun"]["target"] = size + p["growth"]
            bad = c.wait(c.call("PATCH", "/api/storage/luns/" + p["lun_uuid"], body={"space": {"size": t}}), wait)
            got = gb_of([x for x in c.records("/api/storage/luns", LUN_FIELDS, uuid=p["lun_uuid"]) if x.get("uuid") == p["lun_uuid"]][0], "space", "size")
            ok = not bad and got >= t - X.SLACK
            res = bad or ("Done - read back %s" % X.human(got) if ok else "ONTAP said done, but it reads %s" % X.human(got))
    except (Failed, Unreachable, IndexError, HTTPException, ValueError) as e:
        ok, res = False, ("Failed or not known: %s - the volume is grown; read the LUN in ONTAP System Manager and grow it if it is not"
                          % (e or "the LUN could not be read back"))
    steps.append({"step": "Grow the LUN", "exactly": "%s space.size %s -> %s" % (p["lun"]["name"], X.human(p["lun"]["size"]), X.human(t)),
                  "ok": ok, "result": res})
    return steps


def main():
    m = AnsibleModule(
        argument_spec=dict(action=dict(type="str", required=True, choices=["list", "plan", "grow"]),
                           clusters=dict(type="list", elements="str", required=True), choice=dict(type="str", default=""),
                           size=dict(type="str", default=""), comment_line=dict(type="str", default=""),
                           pct=dict(type="float", default=10), min_gb=dict(type="float", default=1), max_gb=dict(type="float", default=512),
                           max_pct=dict(type="float", default=97), exclude=dict(type="list", elements="str", default=[]),
                           lun_mode=dict(type="str", default="same", choices=["same"]), wait_seconds=dict(type="int", default=300),
                           expect_volume_uuid=dict(type="str", default=""), expect_lun_uuid=dict(type="str", default=""),
                           max_vol_growth=dict(type="int", default=-1),
                           validate_certs=dict(type="bool", default=True), ca_path=dict(type="str", default=""),
                           timeout=dict(type="int", default=30), tls_legacy=dict(type="bool", default=False)),
        supports_check_mode=True)
    p = m.params
    user, password = os.environ.get("ONTAP_USERNAME", ""), os.environ.get("ONTAP_PASSWORD", "")
    if not user or not password:
        m.fail_json(msg='No ONTAP account: attach a credential of type "NetApp ONTAP" to this job template (aap/credential_types/netapp_ontap.yml).')
    clusters = [str(x).strip() for x in p["clusters"] if str(x).strip()]
    if not clusters:
        m.fail_json(msg="ontap_clusters is empty: list the clusters' management addresses (docs/NETAPP.md).")
    opts = {k: p[k] for k in ("pct", "min_gb", "max_gb", "max_pct", "exclude", "lun_mode")}
    ctx = ssl_context(p["validate_certs"], p["ca_path"], p["tls_legacy"])

    if p["action"] == "list":
        out, choices, excluded, aggregates = [], [], [], []
        for a in clusters:
            c = Client(a, user, password, ctx, p["timeout"])
            try:
                name, ch, ex, ag = list_cluster(c, opts)
                out.append({"address": a, "name": name, "reachable": True, "error": ""})
                choices += ch
                excluded += ex
                aggregates += ag
            except Unreachable as e:
                out.append({"address": a, "name": a, "reachable": False, "error": explain(str(e), e.kind, a, p["timeout"])})
            except Failed as e:
                out.append({"address": a, "name": a, "reachable": True, "error": str(e)})
        for e in choices:
            e["choice"] = X.choice_text(e)
        m.exit_json(changed=False, clusters=out, choices=choices, excluded=excluded, aggregates=aggregates)

    key = X.parse_choice(p["choice"])
    if not key:
        m.fail_json(msg="Pick the volume from the list (NetApp - refresh volume list writes it): '%s' is not one of its choices."
                    % str(p["choice"]).strip()[:120])
    if key["address"].lower() not in [x.lower() for x in clusters]:
        m.fail_json(msg="The choice names cluster %s, which is not in ontap_clusters - nothing was read or changed." % key["address"])
    c = Client(key["address"], user, password, ctx, p["timeout"])
    try:
        plan, _v = read_one(c, key, opts, p["size"])
        plan.update(cluster=cluster_name(c), address=key["address"])
        if p["action"] == "plan" or m.check_mode or plan["status"] != "grow":
            m.exit_json(changed=False, plan=plan, steps=[], msg="" if plan["status"] != "refused" else plan["why"])
        if p["expect_volume_uuid"] and (plan["volume_uuid"] != p["expect_volume_uuid"] or plan["lun_uuid"] != p["expect_lun_uuid"]):
            m.fail_json(msg="%s on %s is not the volume (or LUN) the plan read: it was replaced since - nothing was changed" % (key["volume"], key["svm"]),
                        plan=plan)
        if p["max_vol_growth"] >= 0:                # the plan the person saw: the volume never grows more than it said
            plan["vol_growth_cap"] = p["max_vol_growth"]
            if plan.get("vol_growth", plan.get("growth", 0)) > p["max_vol_growth"] + X.SLACK:
                m.fail_json(msg="%s needs more now than its plan said (+%s, planned +%s): free space went since - nothing was changed; launch it again"
                            % (key["volume"], X.human(plan.get("vol_growth", plan.get("growth", 0))), X.human(p["max_vol_growth"])), plan=plan)
        steps = grow(c, plan, key, opts, p["comment_line"], p["wait_seconds"])
    except Unreachable as e:
        m.fail_json(msg=explain(str(e), e.kind, key["address"], p["timeout"]))
    except Failed as e:
        m.fail_json(msg="%s (cluster %s) - nothing was changed" % (e, key["address"]))
    changed = any(s["ok"] for s in steps if s["step"].startswith("Grow"))
    m.exit_json(changed=changed, plan=plan, steps=steps, failed=not all(s["ok"] for s in steps),
                msg="" if all(s["ok"] for s in steps) else next(s["result"] for s in steps if not s["ok"]))


if __name__ == "__main__":
    main()
