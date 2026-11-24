#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates, aap_templates_rename (1.79.19): the site's own job templates and workflows renamed to the
catalog's names (aap/job_templates.yml), so they read like the documents and the site portal's cards. Found the way the
descriptions are (site_aap.catalog_entry, 1.79.11): a job template of the project by its playbook (a playbook of several
catalog templates by its name, then its Variables), a workflow by its name, else by the playbooks of its steps.

aap_templates_rename_map {"Their name": "New name"} goes first: it names a template or workflow the catalog does not
know too, and it renames one the rules below leave. Never renamed:
- onto a name AAP already has (a job template or a workflow, any organization: the site portal finds a template by its
  name alone), nor two onto one;
- one of several templates of one catalog template (the same playbook): listed - the map names the one to rename;
- one of an area in aap_templates_rename_skip_areas (its catalog area; "Network" also every template of the Network EE);
- the name of a template or workflow another job makes (made_by, AAP - set up portal actions: it knows its own by name);
- a template or workflow a site portal action names (portal_aap_names, portal_actions_extra, portal_actions): the
  portal looks it up by that name at its next start - renamed only by the map, with the line to change in the same go;
- a template or workflow that is itself one AAP - set up portal actions made (by its current name);
- a workflow with a step that is not a job template of the project or an approval (a nested workflow, an update).
Only the name changes (a PATCH of name): its description, Variables, schedules, survey, credentials and ids stay. Check
mode only reads and lists each rename, old -> new. A rename AAP refuses is FAILED and named; the others still go.
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_rename
short_description: Rename the site's job templates and workflows in AAP to the catalog's names (never deletes)
options:
  catalog: {description: "aap/job_templates.yml's job_templates.", type: list, elements: dict, required: true}
  workflows: {description: "aap/job_templates.yml's workflows: [{name, playbooks, description}].", type: list, elements: dict, default: []}
  organization: {description: The organization (its name)., type: str, required: true}
  project: {description: The site-automation project (its name)., type: str, required: true}
  rename: {description: "Rename by the catalog (aap_templates_rename); false = only the map.", type: bool, default: true}
  rename_map: {description: "{their name: new name} (aap_templates_rename_map) - first, for any template or workflow of the organization.", type: dict, default: {}}
  skip_areas: {description: "Catalog areas left out (aap_templates_rename_skip_areas); Network = also the Network EE's.", type: list, elements: str, default: []}
  made_elsewhere: {description: "Names another job makes and finds by name (AAP - set up portal actions): never renamed onto.", type: list, elements: str, default: []}
  portal_targets: {description: "The site portal's actions as it runs them (portal_actions or the built-ins, portal_actions_extra,
    portal_aap_names applied): [{action, type, name, setting}] - what one names is renamed only by the map.", type: list, elements: dict, default: []}
  only: {description: "Only the templates whose current or new name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each template or workflow looked at: {kind (job|workflow), name, new, catalog, action (rename|right|
  skipped|clash|twice|not found|failed), why}.", type: list}
renamed: {description: "{job: {old: new}, workflow: {old: new}} - renamed (check mode: would be).", type: dict}
twice: {description: "One line per catalog template or workflow the site has several of.", type: list}
portal: {description: "The portal_aap_names lines to change with a rename of the map.", type: list}
problems: {description: "What stopped it before any rename (a setting, AAP not read) and the renames AAP refused.", type: list}
'''

import fnmatch
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, catalog_entry, connection, workflow_fits, workflow_steps

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it renames job templates)')
KINDS = {"job": "job_templates", "workflow": "workflow_job_templates"}
WORD = {"job": "job template", "workflow": "workflow"}


def _q(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def _why(code, d):
    """What AAP refused, by its field names only - never a value it sent back."""
    keys = sorted(str(k) for k in (d or {}) if k != "detail") if isinstance(d, dict) else []
    return "HTTP %d%s" % (code, (" (fields: %s)" % ", ".join(keys)) if keys else "")


def areas_of(e):
    """The areas a catalog entry is in, for aap_templates_rename_skip_areas: its own, and Network for the Network EE's. Network is only
    ever the Network EE's: the catalog's own Network area (Network map, SolarWinds, Cisco UCS on the default EE) is never skipped by it."""
    out = set([str(e.get("area") or "")])
    out = set(x for x in out if x.casefold() != "network")
    if str(e.get("ee") or "") == "network":
        out.add("Network")
    return set(x.casefold() for x in out if x)


class Rename(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.results, self.problems, self.twice, self.portal = [], [], [], []
        self.renamed = {"job": {}, "workflow": {}}
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
        raise RuntimeError("%s: more than 500 pages - not all read" % path.split("?")[0])

    def note(self, kind, name, action, new="", catalog="", why=""):
        self.results.append({"kind": kind, "name": name, "new": new, "catalog": catalog, "action": action, "why": why})

    def wanted(self, *names):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(str(n or "").casefold(), x) for n in names if n for x in pats)

    def portal_names(self):
        """The portal's actions -> {(kind, name): [(setting, action)]} (Codex: every action, not only portal_aap_names)."""
        out = {}
        for a in self.p["portal_targets"] or []:
            if not isinstance(a, dict):
                continue
            typ, name = a.get("type"), a.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            for kind in ([typ] if typ in KINDS else list(KINDS)):
                out.setdefault((kind, name.strip()), []).append((str(a.get("setting") or "portal_actions"), str(a.get("action") or "")))
        return out

    # ---- the plan: everything read and decided before any rename
    def plan(self):
        p = self.p
        rmap = p["rename_map"] or {}
        bad = [k for k, v in rmap.items() if not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()]
        if bad:
            self.problems.append("aap_templates_rename_map: each entry is \"their name\": \"new name\" (text) - %s"
                                 % ", ".join(repr(k) for k in bad[:5]))
            return []
        stripped = [k.strip() for k in rmap]
        twice = sorted(set(k for k in stripped if stripped.count(k) > 1))
        if twice:                                # Codex: "Old" and " Old " are one template - never renamed twice
            self.problems.append("aap_templates_rename_map: %s named more than once (spaces at the ends do not count)"
                                 % ", ".join(repr(k) for k in twice))
            return []
        orgs = [o for o in self.all("organizations/?name=%s" % _q(p["organization"])) if o.get("name") == p["organization"]]
        if len(orgs) != 1:
            self.problems.append("organization %r (aap_templates_organization): %s" % (p["organization"], "not found" if not orgs else "named twice"))
            return []
        org = int(orgs[0]["id"])
        projs = [x for x in self.all("projects/?name=%s" % _q(p["project"])) if x.get("name") == p["project"] and x.get("organization") == org]
        if len(projs) != 1:
            self.problems.append("project %r (aap_templates_project) in the organization: %s" % (p["project"], "not found" if not projs else "named twice"))
            return []
        proj = int(projs[0]["id"])
        # every name AAP shows, any organization: the site portal looks a template up by its name alone
        every = {"job": self.all("job_templates/"), "workflow": self.all("workflow_job_templates/")}
        objs = {"job": [t for t in every["job"] if t.get("organization") == org],
                "workflow": [w for w in every["workflow"] if w.get("organization") == org]}
        taken = {}                              # Codex: a name across both kinds - never a job onto a workflow's name
        for k in KINDS:
            for o in every[k]:
                taken.setdefault(str(o.get("name") or ""), []).append((k, int(o["id"])))
        portal = self.portal_names()
        made = set(str(x) for x in p["made_elsewhere"] or [])
        plans, seen = [], set()                 # plans: (kind, obj, new, catalog, how)

        # 1. the map: any template or workflow of the organization, by its name
        for old, new in sorted(rmap.items()):
            old, new = old.strip(), new.strip()
            hits = [(k, o) for k in KINDS for o in objs[k] if o.get("name") == old]
            done = [(k, o) for k in KINDS for o in objs[k] if o.get("name") == new] if not hits else []
            for k, o in hits + done:             # Codex: the map's, whatever only says - never renamed by the catalog instead
                seen.add((k, int(o["id"])))
            if not self.wanted(old, new):
                continue
            if not hits:
                if done:                         # renamed before (a run of the map already went): kept for the map (Codex)
                    self.note(done[0][0], new, "right", new, why="aap_templates_rename_map: %r is not there, %r is" % (old, new))
                else:
                    self.note("job", old, "not found", new, why="aap_templates_rename_map: no job template or workflow of that name in the organization")
                continue
            if len(hits) > 1:
                self.note(hits[0][0], old, "skipped", new, why="aap_templates_rename_map: %d of that name (%s) - rename one in AAP first"
                          % (len(hits), ", ".join(WORD[k] for k, _ in hits)))
                continue
            k, o = hits[0]
            plans.append((k, o, new, "", "aap_templates_rename_map"))

        # 2. the job templates of the project, by their playbook
        if p["rename"]:
            by_pb = {}
            for e in p["catalog"]:
                by_pb.setdefault(str(e.get("playbook") or ""), []).append(e)
            groups = {}
            for t in sorted(objs["job"], key=lambda x: str(x.get("name") or "").casefold()):
                if t.get("project") != proj:
                    continue
                e = catalog_entry(t, by_pb.get(str(t.get("playbook") or "")))
                if e is not None:
                    groups.setdefault(e["name"], (e, []))[1].append(t)
            for name, (e, ts) in sorted(groups.items()):
                self.decide("job", e, ts, plans, made, portal, str(e.get("playbook")), seen)

            # 3. the workflows: by name, else by the playbooks of their steps (one fit only)
            pb_of = dict((int(t["id"]), str(t.get("playbook") or "")) for t in objs["job"] if t.get("project") == proj)
            cat = [w for w in p["workflows"] or [] if isinstance(w, dict) and w.get("name")]
            entry_area = {}
            for e in p["catalog"]:
                entry_area.setdefault(str(e.get("playbook") or ""), set()).update(areas_of(e))
            wgroups = {}
            for w in sorted(objs["workflow"], key=lambda x: str(x.get("name") or "").casefold()):
                entry = next((c for c in cat if c["name"] == w.get("name")), None)
                if entry is None:
                    nodes = self.all("workflow_job_template_nodes/?workflow_job_template=%d" % int(w["id"]))
                    steps, foreign, approval = workflow_steps(nodes, pb_of)
                    # Codex: every step a job template of the project or an approval - a nested workflow or an update
                    # does more than the catalog's: never renamed by its steps
                    other = [n for n in nodes if ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get(
                        "unified_job_type") not in ("job", "workflow_approval")]
                    if not steps or foreign or other:
                        continue
                    fits = workflow_fits(steps, approval, cat, self.wants_approval)
                    if len(fits) > 1:
                        if self.wanted(w.get("name")):
                            self.note("workflow", str(w.get("name") or ""), "skipped", why="its steps fit several workflows of the "
                                      "catalog (%s) - aap_templates_rename_map names the one" % ", ".join(repr(c["name"]) for c in fits[:4]))
                        continue
                    if not fits:
                        continue
                    entry = fits[0]
                # a workflow's areas: those of every step's playbook - all of them skipped, it is skipped
                areas = [entry_area.get(pb, set()) for pb in entry.get("playbooks") or [] if pb != "playbooks/workflow_gate.yml"]
                e = dict(entry, _areas=set.intersection(*areas) if areas else set())
                wgroups.setdefault(entry["name"], (e, []))[1].append(w)
            for name, (e, ws) in sorted(wgroups.items()):
                self.decide("workflow", e, ws, plans, made, portal, "the steps of '%s'" % name, seen)

        # 4. clashes: onto a name AAP has (another object of that kind, any organization), or two onto one
        onto = {}
        for k, o, new, cat_name, how in plans:
            onto.setdefault(new, []).append(o)
        out = []
        for k, o, new, cat_name, how in plans:
            name = str(o.get("name") or "")
            if new == name:
                self.note(k, name, "right", new, cat_name, why=how if how == "aap_templates_rename_map" else "")
                continue
            others = [x for x in taken.get(new, []) if x != (k, int(o["id"]))]
            if others:
                self.note(k, name, "clash", new, cat_name, why="AAP has a %s named %r already%s - not renamed; rename or remove that one first"
                          % (WORD[others[0][0]], new, "" if len(others) == 1 else " (%d of them)" % len(others)))
                continue
            if len(onto[new]) > 1:
                self.note(k, name, "clash", new, cat_name, why="%s would all be named %r - not renamed; aap_templates_rename_map names one"
                          % (", ".join(repr(str(x.get("name"))) for x in onto[new]), new))
                continue
            actions = portal.get((k, name), [])
            if actions:
                said = "; ".join("%s: %s" % (st, ", ".join(a for s2, a in actions if s2 == st)) for st in sorted(set(s2 for s2, _ in actions)))
                if how != "aap_templates_rename_map":
                    self.note(k, name, "skipped", new, cat_name, why="a site portal action runs it by that name (%s): "
                              "aap_templates_rename_map renames it - then change that setting to %r" % (said, new))
                    continue
                self.portal.append("%s - change %r to %r (the site portal looks it up by that name at its next start)" % (said, name, new))
            out.append((k, o, new, cat_name, how))
        return out

    @staticmethod
    def wants_approval(c):
        """A catalog workflow that promises an approval - "No approval" is not one (Codex: Certificate watch)."""
        t = str((c.get("description") or {}).get("supports") or "").lower()
        return "approv" in re.sub(r"\bno approvals?\b", "", t)

    def decide(self, kind, e, objs, plans, made, portal, what, seen):
        """One catalog template or workflow and the site's ones found for it: renamed (one), right, or why not. One the map
        renames (seen) is the map's; it still counts as one of several - the others are then listed, never renamed."""
        name = e["name"]
        names = [str(o.get("name") or "") for o in objs]
        if not self.wanted(name, *names):
            return
        right = [o for o in objs if o.get("name") == name and (kind, int(o["id"])) not in seen]
        for o in right:
            self.note(kind, name, "right", name, name)
        rest = [o for o in objs if o.get("name") != name and (kind, int(o["id"])) not in seen]
        # Codex: one AAP - set up portal actions made (its current name is one of that job's) stays as it is
        for o in [o for o in rest if str(o.get("name") or "") in made]:
            self.note(kind, str(o.get("name") or ""), "skipped", name, name, why="AAP - set up portal actions made it and knows it by "
                      "that name")
        rest = [o for o in rest if str(o.get("name") or "") not in made]
        if not rest:
            return
        areas = e.get("_areas") if kind == "workflow" else areas_of(e)
        hit = sorted(str(a) for a in self.p["skip_areas"] or [] if str(a).casefold() in areas)
        if hit:
            for o in rest:
                self.note(kind, str(o.get("name") or ""), "skipped", name, name, why="area %s (aap_templates_rename_skip_areas)" % hit[0])
            return
        if len(objs) > 1:
            listed = ", ".join("'%s'" % n for n in sorted(names, key=str.casefold))
            for o in rest:
                self.note(kind, str(o.get("name") or ""), "twice", name, name, why="%d %ss run %s - aap_templates_rename_map names the one"
                          % (len(objs), WORD[kind], what))
            mapped = [o for o in objs if (kind, int(o["id"])) in seen]
            self.twice.append("%d %ss run %s: %s - %s" % (
                len(objs), WORD[kind], what, listed,
                ("%r has the catalog's name; the other%s left" % (name, "s" if len(rest) > 1 else "")) if right
                else ("aap_templates_rename_map renames %s; the other%s left" % (", ".join(repr(str(o.get("name"))) for o in mapped),
                                                                              "s" if len(rest) > 1 else "")) if mapped
                else "name one in aap_templates_rename_map"))
            return
        if e.get("made_by") or name in made:
            for o in rest:
                self.note(kind, str(o.get("name") or ""), "skipped", name, name, why="%s makes %r and knows it by that name - "
                          "aap_templates_rename_map renames yours anyway" % (e.get("made_by") or "AAP - set up portal actions", name))
            return
        plans.append((kind, rest[0], name, name, "the playbook" if kind == "job" else "its steps"))

    def run(self):
        steps = self.plan()
        if self.problems:
            return
        for k, o, new, cat_name, how in steps:
            name = str(o.get("name") or "")
            why = "by %s" % how if how != "aap_templates_rename_map" else how
            if self.check:
                self.renamed[k][name] = new
                self.note(k, name, "rename", new, cat_name, why)
                continue
            code, d = self.api.call("PATCH", "%s/%d/" % (KINDS[k], int(o["id"])), {"name": new})
            if code not in (200, 201, 202, 204):
                self.note(k, name, "failed", new, cat_name, _why(code, d))
                self.problems.append("%s %r: not renamed to %r: %s" % (WORD[k], name, new, _why(code, d)))
                continue
            self.wrote = True
            self.renamed[k][name] = new
            self.note(k, name, "rename", new, cat_name, why)


def main():
    module = AnsibleModule(
        argument_spec=dict(catalog=dict(type="list", elements="dict", required=True), workflows=dict(type="list", elements="dict", default=[]),
                           organization=dict(type="str", required=True), project=dict(type="str", required=True),
                           rename=dict(type="bool", default=True), rename_map=dict(type="dict", default={}),
                           skip_areas=dict(type="list", elements="str", default=[]),
                           made_elsewhere=dict(type="list", elements="str", default=[]),
                           portal_targets=dict(type="list", elements="dict", default=[]),
                           only=dict(type="list", elements="str", default=[]),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_templates_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], renamed={"job": {}, "workflow": {}}, twice=[], portal=[], problems=[why])
    s = Rename(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as ex:
        s.problems.append(str(ex))
    order = {"rename": 0, "failed": 0, "clash": 1, "twice": 2, "skipped": 3, "not found": 4, "right": 5}
    s.results.sort(key=lambda r: (order.get(r["action"], 9), r["kind"], str(r["name"]).casefold()))
    # Codex: a Check run that would rename says changed, as one that would write does
    changed = s.wrote or (module.check_mode and any(s.renamed[k] for k in s.renamed))
    module.exit_json(changed=changed, results=s.results, renamed=s.renamed, twice=s.twice, portal=s.portal, problems=s.problems)


if __name__ == "__main__":
    main()
