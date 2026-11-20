#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - move templates to another inventory (playbooks/aap_inventory_move.yml): every job template, workflow, workflow
node and schedule of the organization whose inventory is one named inventory gets another one instead. Names are exact.
Everything is read and checked first - an inventory not found, more moves than the cap - and nothing is written on a
problem; in check mode nothing is written. Nothing else is changed: only the inventory field of those objects. A Limit
that names a group or host the new inventory lacks is warned about (the move is still made). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_inventory_move
short_description: Move AAP job templates, workflows, workflow nodes and schedules from one inventory to another
options:
  replace: {description: "[{from: inventory name, to: inventory name, from_organization, to_organization}] - exact names; each in the organization unless its own is named.", type: list, elements: dict, required: true}
  organization: {description: The organization (its name)., type: str, required: true}
  only: {description: "Only the templates and workflows whose name matches one of these patterns (* and ?, any case); a node:
    its workflow's name; a schedule: its template's name. [] = all.", type: list, elements: str, default: []}
  max: {description: "More moves than this stop the job before any write.", type: int, default: 100}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each move: {kind (job template|workflow|workflow node|schedule), name, from, to, state (would move|moved|
  failed), why, warn}.", type: list}
problems: {description: "What stopped the job: a name not found, too many moves, AAP not reached, a write that failed.", type: list}
'''

import fnmatch
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it changes job templates and workflows)')


def _q(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def _why(code, d):
    """What AAP refused, by its field names only - never a value it sent back."""
    keys = sorted(str(k) for k in (d or {}) if k != "detail") if isinstance(d, dict) else []
    return "HTTP %d%s" % (code, (" (fields: %s)" % ", ".join(keys)) if keys else "")


def limit_names(text):
    """A Limit -> the plain group and host names it names ('web:&prod:!old' -> web, prod, old); a pattern (*, ?, ~, [) is
    left out: it is not a name to look for."""
    out = []
    for tok in re.split(r"[,:]", str(text or "")):
        tok = tok.strip().lstrip("!&").strip()
        if tok and tok != "all" and not re.search(r"[*?~\[\]]", tok) and tok not in out:
            out.append(tok)
    return out


class Move(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.results, self.problems = [], []
        self.wrote = False

    def all(self, path):
        out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
        for _ in range(500):
            code, d = self.api.call("GET", nxt)
            if code != 200:
                raise RuntimeError("%s could not be read: %s" % (path.split("?")[0], _why(code, d)))
            out += d.get("results") or []
            n = d.get("next")
            if not n:
                return out
            nxt = n.split(self.api.base, 1)[1] if self.api.base in n else n.lstrip("/")
        return out

    def get(self, path):
        code, d = self.api.call("GET", path)
        if code != 200:
            raise RuntimeError("%s could not be read: %s" % (path, _why(code, d)))
        return d

    def org_named(self, name):
        """An organization's id by its exact name (cached), or None with the problem noted."""
        if name not in self._orgs:
            orgs = [o for o in self.all("organizations/?name=%s" % _q(name)) if o.get("name") == name]
            if len(orgs) != 1:
                self.problems.append("organization %r: %s" % (name, "not found" if not orgs else "%d match" % len(orgs)))
            self._orgs[name] = int(orgs[0]["id"]) if len(orgs) == 1 else None
        return self._orgs[name]

    def inventory(self, name, which, org=None):
        """An inventory by its exact name in an organization: the job's own, or the entry's from_organization /
        to_organization (an inventory of another organization: AAP lets a template use it when the credential's user has
        Use on it)."""
        org = org or self.p["organization"]
        oid = self.org_named(org)
        if oid is None:
            return None
        rows = [r for r in self.all("inventories/?name=%s" % _q(name)) if r.get("name") == name and r.get("organization") == oid]
        if len(rows) != 1:
            self.problems.append("aap_inventory_replace %s %r: %s" % (
                which, name, ("not found in organization %r" % org) if not rows else "%d match - rename one in AAP" % len(rows)))
            return None
        return int(rows[0]["id"])

    def wanted(self, name):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(str(name).casefold(), x) for x in pats)

    def parent(self, kind, oid):
        """A workflow or job template by id (cached) - its organization and name."""
        key = (kind, oid)
        if key not in self._parents:
            self._parents[key] = self.get("%s/%d/" % (kind, oid))
        return self._parents[key]

    # ---- the plan: every read and every check before any write
    def plan(self):
        p = self.p
        self._orgs = {}
        self.org_id = self.org_named(p["organization"])
        if self.org_id is None:
            return []
        self._parents = {}
        pairs, froms = [], set()
        for n, r in enumerate(p["replace"] or [], 1):
            r = r if isinstance(r, dict) else {}
            a, b = str(r.get("from") or "").strip(), str(r.get("to") or "").strip()
            if not a or not b:
                self.problems.append("aap_inventory_replace entry %d: both from and to are needed" % n)
                continue
            fo, to = str(r.get("from_organization") or "").strip(), str(r.get("to_organization") or "").strip()
            fid, tid = self.inventory(a, "from", fo), self.inventory(b, "to", to)
            if fid is None or tid is None:
                continue
            # the same inventory, and a source named twice: by the inventories found (one name in two organizations is two)
            if fid == tid:
                self.problems.append("aap_inventory_replace entry %d: from and to are the same inventory (%r)" % (n, a))
                continue
            if fid in froms:
                self.problems.append("aap_inventory_replace: %r named twice as from" % a)
                continue
            froms.add(fid)
            pairs.append((a, fid, b, tid))
        if self.problems:
            return []
        moves = []
        for a, fid, b, tid in pairs:
            for kind, path, label in (("job_templates", "job_templates", "job template"),
                                      ("workflow_job_templates", "workflow_job_templates", "workflow")):
                for t in self.all("%s/?inventory=%d" % (kind, fid)):
                    if t.get("inventory") == fid and t.get("organization") == self.org_id and self.wanted(t.get("name")):
                        moves.append({"kind": label, "name": t.get("name"), "path": "%s/%d/" % (path, int(t["id"])), "from": a,
                                      "to": b, "to_id": tid, "limit": t.get("limit") or ""})
            for n in self.all("workflow_job_template_nodes/?inventory=%d" % fid):
                if n.get("inventory") != fid or not n.get("workflow_job_template"):
                    continue
                wf = self.parent("workflow_job_templates", int(n["workflow_job_template"]))
                if wf.get("organization") != self.org_id or not self.wanted(wf.get("name")):
                    continue
                what = ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get("name") or n.get("identifier") or n.get("id")
                moves.append({"kind": "workflow node", "name": "%s (in workflow %s)" % (what, wf.get("name")),
                              "path": "workflow_job_template_nodes/%d/" % int(n["id"]), "from": a, "to": b, "to_id": tid,
                              "limit": n.get("limit") or ""})
            for sc in self.all("schedules/?inventory=%d" % fid):
                if sc.get("inventory") != fid:
                    continue
                ujt = (sc.get("summary_fields") or {}).get("unified_job_template") or {}
                kind = {"job": "job_templates", "workflow_job": "workflow_job_templates"}.get(ujt.get("unified_job_type"))
                if not kind or not sc.get("unified_job_template"):
                    continue
                par = self.parent(kind, int(sc["unified_job_template"]))
                if par.get("organization") != self.org_id or not self.wanted(par.get("name")):
                    continue
                moves.append({"kind": "schedule", "name": "%s (of %s)" % (sc.get("name"), par.get("name")),
                              "path": "schedules/%d/" % int(sc["id"]), "from": a, "to": b, "to_id": tid, "limit": sc.get("limit") or ""})
        if len(moves) > p["max"]:
            self.problems.append("%d to move, more than aap_inventory_replace_max (%d): nothing moved - narrow it with "
                                 "aap_inventory_replace_only, or raise aap_inventory_replace_max" % (len(moves), p["max"]))
            return []
        # the Limit: a group or host it names that the new inventory lacks is warned about (the move is still made)
        seen = {}
        for m in moves:
            lack = []
            for nm in limit_names(m["limit"]):
                k = (m["to_id"], nm)
                if k not in seen:
                    seen[k] = bool([g for g in self.all("inventories/%d/groups/?name=%s" % (m["to_id"], _q(nm))) if g.get("name") == nm]
                                   or [h for h in self.all("inventories/%d/hosts/?name=%s" % (m["to_id"], _q(nm))) if h.get("name") == nm])
                if not seen[k]:
                    lack.append(nm)
            if lack:
                m["warn"] = "its Limit names %s: no group or host of %s in %r" % (
                    ", ".join(repr(x) for x in lack), "that name" if len(lack) == 1 else "those names", m["to"])
        return moves

    def note(self, m, state, why=""):
        self.results.append({"kind": m["kind"], "name": m["name"], "from": m["from"], "to": m["to"], "state": state, "why": why,
                             "warn": m.get("warn", "")})

    def run(self):
        moves = self.plan()
        if self.problems:
            return
        for m in moves:
            if self.check:
                self.note(m, "would move")
                continue
            code, d = self.api.call("PATCH", m["path"], {"inventory": m["to_id"]})
            if code not in (200, 201, 202, 204):
                self.note(m, "failed", _why(code, d))
                self.problems.append("stopped at %s %r: %s (the moves before it are listed as moved)" % (m["kind"], m["name"], _why(code, d)))
                return
            self.wrote = True
            self.note(m, "moved")


def main():
    module = AnsibleModule(
        argument_spec=dict(replace=dict(type="list", elements="dict", required=True), organization=dict(type="str", required=True),
                           only=dict(type="list", elements="str", default=[]), max=dict(type="int", default=100),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_inventory_replace_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], problems=[why])
    s = Move(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as ex:
        s.problems.append(str(ex))
    module.exit_json(changed=s.wrote or (module.check_mode and bool(s.results)), results=s.results, problems=s.problems)


if __name__ == "__main__":
    main()
