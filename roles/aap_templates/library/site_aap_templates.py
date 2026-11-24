#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates (playbooks/aap_templates_setup.yml): the job templates of aap/job_templates.yml (the catalog,
one per template of docs/JOB_TEMPLATES.md), made in AAP through its own API, or checked against it. A missing one is made;
one that differs is listed field by field and brought right only with overwrite. Never deletes; never touches a
template's schedules, survey or Variables after it is made, nor a credential of a type the catalog does not name. A
template running the same playbook under another name is listed, not made twice. Everything is read and checked first;
in check mode nothing is written. Only a credential's id, name and type are read out. 1.79.19: match brings named
fields right without overwrite (credentials only added); renamed reads a Check run's renames (site_aap_rename) as done;
create_missing: false makes no template. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_templates
short_description: Make or check the site-automation job templates in AAP from the catalog (never deletes)
options:
  catalog: {description: "aap/job_templates.yml's job_templates.", type: list, elements: dict, required: true}
  organization: {description: The organization (its name)., type: str, required: true}
  project: {description: The site-automation project (its name)., type: str, required: true}
  inventory: {description: "The inventory a template is made with (its name).", type: str, default: ''}
  inventory_organization: {description: "1.79.11c: the inventory's organization when it is not the templates' ('' = the same).", type: str, default: ''}
  ee: {description: "{all: name, windows: name, network: name} - an execution environment per kind ('' = the project's).", type: dict, default: {}}
  credentials: {description: "{kind: credential name} (a list of names for several).", type: dict, default: {}}
  only: {description: "Only the templates whose name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  overwrite: {description: "Bring a template that differs right (only the fields this job sets).", type: bool, default: false}
  create_alias: {description: "Make the catalog's template even when its playbook already runs under another name (for a
    workflow of the site's own that names the catalog's).", type: bool, default: false}
  label_portal: {description: "none, portal-actions (the templates the site portal launches) or all (every template made or matched):
    the portal's label (portal_label) and portal-area:<area>, so the site portal finds them.", type: str, default: none}
  portal_label: {description: "The site portal's own label for the templates it reads (matched as written, case too).", type: str, default: portal}
  portal_label_area: {description: "The prefix of the site portal's group label (portal-area:<area>).", type: str, default: "portal-area:"}
  portal_needed: {description: "1.107.0: the steps of the catalog workflows the portal shows (portal: true) - never labelled portal. Not given: needed.", type: list, elements: str}
  needed: {description: "1.83.0: the templates the catalog's workflows need (their nodes). One another job makes (made_by) that AAP has not under its name: a template of the site that runs its playbook is used for the step (said: uses ...), else it is made here.", type: list, elements: str, default: []}
  step_names: {description: "1.83.0: {a step's catalog name: the site's template to use for it} - when several of the site's templates run its playbook.", type: dict, default: {}}
  variables_dir: {description: "The folder of the catalog's Variables files (the repository's root).", type: str, default: ''}
  match: {description: "1.79.19 (aap_templates_match): the fields brought to the catalog's on the templates there, without overwrite -
    inventory, credentials (the missing kinds added, none taken off), prompts, ee, labels (the portal's).", type: list, elements: str, default: []}
  match_skip_areas: {description: "1.79.19: the catalog areas match leaves out (aap_templates_rename_skip_areas; Network = also the
    Network EE's).", type: list, elements: str, default: []}
  create_missing: {description: "1.79.19: false = a catalog template not in AAP is not made, only counted (renames and match
    only).", type: bool, default: true}
  renamed: {description: "1.79.19: {old: new} - the renames a Check run of aap_templates_rename would make: the templates are read as
    renamed.", type: dict, default: {}}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each template: {name, action (create|fix|right|differs|other name|skipped|left|missing|failed), diff ({field:
  [has, should be]}), why, labels (the portal labels it gets, or would)}.", type: list}
problems: {description: "What stopped the job before any write: a name not found, AAP not reached.", type: list}
stale_labels: {description: "Lines naming an old catalog area's portal-area label on a template, to remove by hand (never taken off).", type: list}
'''

import fnmatch
import json
import os
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection
from ansible.module_utils.site_aap_workflows import NEVER_ALONE, limit_misses_localhost, survey_question

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it makes job templates)')
DESC = "Made by AAP - set up job templates (aap/job_templates.yml)"
SHOW = {"playbook": "playbook", "project": "project", "execution_environment": "execution environment",
        "ask_limit_on_launch": "prompt for Limit", "ask_variables_on_launch": "prompt for Variables",
        "ask_inventory_on_launch": "prompt for Inventory", "ask_job_type_on_launch": "prompt for Job Type", "inventory": "inventory",
        "ask_diff_mode_on_launch": "prompt for Show changes", "job_type": "Job type", "diff_mode": "Show changes",
        "extra_vars": "Variables"}
# 1.79.19: aap_templates_match's words -> the fields (credentials and labels apart); playbook and project never: they are
# what a template is found by
MATCH = {"inventory": ("inventory",), "prompts": ("ask_limit_on_launch", "ask_variables_on_launch", "ask_inventory_on_launch",
                                                  "ask_job_type_on_launch", "ask_diff_mode_on_launch"),
         "ee": ("execution_environment",), "credentials": (), "labels": ()}


def _q(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def _why(code, d):
    """What AAP refused, by its field names only - never a value it sent back."""
    keys = sorted(str(k) for k in (d or {}) if k != "detail") if isinstance(d, dict) else []
    return "HTTP %d%s" % (code, (" (fields: %s)" % ", ".join(keys)) if keys else "")


def off(text):
    """A Variables file with every setting switched off: what it reads, never a value that runs (an example there would)."""
    out = []
    for line in str(text or "").splitlines():
        s = line.strip()
        out.append(line if (not s or s.startswith("#") or s == "---") else "# " + line)
    return "\n".join(out) + "\n"


# the areas the catalog gave before one section per platform: a portal-area: label of one of these that is not the entry's area
# now is most likely this job's own old label - but AAP - set up portal actions (portal_publish_area) or a person may have put the
# same text there, so it is never taken off: the job names it, to remove by hand
OLD_CATALOG_AREAS = ("STIG", "Disk & services", "fapolicyd", "Tickets", "ACT", "Linux", "Windows", "VMware", "Reports")


def stale_area_labels(have, prefix, area):
    """The labels of a template (names) that look like this job's old portal-area: label: prefix + an old catalog area that is not
    its area now - sorted; none without a prefix or an area."""
    if not prefix or not area:
        return []
    old = set(x.casefold() for x in OLD_CATALOG_AREAS)
    return sorted(n for n in have if n.startswith(prefix) and n[len(prefix):].strip().casefold() in old
                  and n[len(prefix):].strip().casefold() != str(area).strip().casefold())


def areas_of(e):
    """A catalog entry's areas for aap_templates_rename_skip_areas: its own, and Network for the Network EE's (as the renames). Network is only
    ever the Network EE's: the catalog's own Network area (Network map, SolarWinds, Cisco UCS on the default EE) is never skipped by it."""
    # skip_areas: its area before one section per platform and its area now (a site's [Reports] still leaves Log analysis out)
    out = set(str(x or "") for x in (e.get("skip_areas") or [])) | set([str(e.get("area") or "")])
    out = set(x for x in out if x.casefold() != "network")
    if str(e.get("ee") or "") == "network":
        out.add("Network")
    return set(x.casefold() for x in out if x)


def _flat(v):
    """A setting's value as text, to compare: true / false for a boolean, '' for none."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def _as_json(text):
    """AAP's Variables as a mapping when they are JSON (or empty), else None."""
    if isinstance(text, dict):
        return dict(text)
    if not str(text or "").strip():
        return {}
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _top(key):
    """A top-level YAML key's line, and the indented lines under it."""
    return re.compile(r"(?m)^%s:[^\n]*(?:\n[ \t]+[^\n]*)*" % re.escape(key))


def vars_of(text):
    """1.83.1 (P2-1): a template's Variables as AAP has them (JSON or YAML text) -> {top-level key: its value as text}.
    Standard library only: JSON, else each top-level "key: value" line (a value over several lines reads as '')."""
    d = _as_json(text)
    if d is None:
        d = {}
        for m in re.finditer(r"(?m)^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*?)[ \t]*$", str(text)):
            v = re.sub(r"\s+#.*$", "", m.group(2))
            try:
                v = json.loads(v)
            except ValueError:
                v = v[1:-1] if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"" else v
            d.setdefault(m.group(1), v)
    return dict((k, _flat(v)) for k, v in d.items())


def set_vars(text, fixed):
    """1.83.1 (P2-1): the template's Variables with the catalog's fixed settings set - only those keys changed, the rest of
    the text (a person's settings, the comments) kept as it is."""
    d = _as_json(text)
    if d is not None and str(text or "").strip():
        d.update(fixed)
        return json.dumps(d, indent=2)
    out, missing = str(text or ""), []
    for k, v in sorted(fixed.items()):
        line = "%s: %s" % (k, json.dumps(v))
        out, n = _top(k).subn(lambda m, line=line: line, out, count=1)
        if not n:
            missing.append(line)
    if missing:
        head = "\n".join(missing) + "\n"
        m = re.match(r"---[ \t]*\n", out)
        out = (out[:m.end()] + head + out[m.end():]) if m else ("---\n" + head + out)
    return out


class Templates(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.uses, self.renamed = {}, []       # 1.83.0: {a step's catalog name: the site's template used}; old names to rename
        self.refused = []                      # 1.83.0 (Codex P1, final pass): steps not fit to wire - their workflows not made
        self.results, self.problems = [], []

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

    def one(self, kind, name, what, org=True):
        """The one object of that name in the organization (Codex: never one of another organization instead, nor a
        personal credential). Only an execution environment may also be of no organization (a global one)."""
        rows = [r for r in self.all("%s/?name=%s" % (kind, _q(name))) if r.get("name") == name]
        if org and self.org_id is not None:
            ok = (self.org_id, None) if kind == "execution_environments" else (self.org_id,)
            rows = [r for r in rows if r.get("organization") in ok]
        if len(rows) != 1:
            self.problems.append("%s %r: %s" % (what, name, ("not found" + self.in_org(org)) if not rows else "%d match - rename one in AAP" % len(rows)))
            return None
        return rows[0]

    def in_org(self, org):
        return (" in organization %r" % self.p["organization"]) if org and self.org_id is not None else ""

    def note(self, name, action, diff=None, why=""):
        self.results.append({"name": name, "action": action, "diff": diff or {}, "why": why})
        return self.results[-1]

    def workflow_steps(self):
        """1.92.0 review: {template name: [the workflows it is a step of]} - the portal's own workflows
        (roles/site_portal_stack/files/workflows.yml) and the catalog's the portal shows (aap/job_templates.yml, portal: true -
        1.107.0: a step of the catalog's other workflows, Health check in Daily health, is labelled as before), as the
        repository has them."""
        out = {}
        root = self.p.get("variables_dir") or ""
        try:
            import yaml
        except ImportError:                              # pragma: no cover - PyYAML comes with ansible-core
            return out
        for rel, key in (("roles/site_portal_stack/files/workflows.yml", "workflows"), ("aap/job_templates.yml", "workflows")):
            path = os.path.join(root, rel)
            if not root or not os.path.isfile(path):
                continue
            with open(path) as fh:
                doc = yaml.safe_load(fh) or {}
            for w in doc.get(key) or []:
                if rel.startswith("aap/") and not (w or {}).get("portal"):
                    continue
                for n in (w or {}).get("nodes") or []:
                    t = (n or {}).get("template")
                    if t:
                        names = out.setdefault(t, [])
                        if w.get("name") not in names:
                            names.append(w.get("name"))
        return out

    def portal_needed(self):
        """1.107.0: the steps of the catalog workflows the portal shows - never labelled (the older runs: every step)."""
        pn = self.p.get("portal_needed")
        return pn if pn is not None else (self.p.get("needed") or [])

    def write(self, method, path, body=None):
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: %s" % (method, path.split("?")[0], _why(code, d)))
        self.wrote = True
        return d

    # ---- what the settings name: resolved first, every one
    def resolve(self):
        p = self.p
        self.org_id = None
        o = self.one("organizations", p["organization"], "organization", org=False)
        self.org_id = int(o["id"]) if o else None
        pr = self.one("projects", p["project"], "project (aap_templates_project)")
        self.project = int(pr["id"]) if pr else None
        self.inventory = None
        if p["inventory"]:
            io = (p.get("inventory_organization") or "").strip()
            if io and io != p["organization"]:
                # 1.79.11c: an inventory of another organization (AAP lets a template use it when the credential's user
                # has Use on it): looked up by its exact name in that organization only
                orgs = [o for o in self.all("organizations/?name=%s" % _q(io)) if o.get("name") == io]
                rows = [r for r in self.all("inventories/?name=%s" % _q(p["inventory"])) if r.get("name") == p["inventory"]
                        and len(orgs) == 1 and r.get("organization") == orgs[0].get("id")]
                if len(orgs) != 1 or len(rows) != 1:
                    self.problems.append("inventory (aap_templates_inventory) %r: %s" % (p["inventory"], (
                        "organization %r (aap_templates_inventory_organization) %s" % (io, "not found" if not orgs else "%d match" % len(orgs)))
                        if len(orgs) != 1 else ("not found in organization %r" % io) if not rows else "%d match - rename one in AAP" % len(rows)))
                inv = rows[0] if len(orgs) == 1 and len(rows) == 1 else None
            else:
                inv = self.one("inventories", p["inventory"], "inventory (aap_templates_inventory)")
            self.inventory = int(inv["id"]) if inv else None
        self.ees = {}
        for kind, name in sorted((p["ee"] or {}).items()):
            if name:
                e = self.one("execution_environments", str(name), "execution environment %s (aap_templates_ee)" % kind)
                if e:
                    self.ees[kind] = (int(e["id"]), str(name))
        self.creds = {}                       # kind -> [(id, name, type, type name)]
        for kind, names in sorted((p["credentials"] or {}).items()):
            names = [names] if isinstance(names, str) else list(names or [])
            got = []
            for n in names:
                c = self.one("credentials", str(n), "credential %s (aap_templates_credentials)" % kind)
                if c:
                    tn = str(((c.get("summary_fields") or {}).get("credential_type") or {}).get("name") or c.get("credential_type"))
                    got.append((int(c["id"]), str(n), c.get("credential_type"), tn))
            if got:
                self.creds[kind] = got

    def wanted(self, e):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(e["name"].casefold(), x) for x in pats)

    def matched(self, e):
        """aap_templates_match's words for this catalog entry - none for an area aap_templates_rename_skip_areas leaves out."""
        skip = set(str(x).casefold() for x in self.p.get("match_skip_areas") or [])
        if areas_of(e) & skip:
            return []
        return [w for w in self.p.get("match") or [] if w in MATCH]

    @staticmethod
    def multi(type_name):
        """A credential type a job template may carry more than one of (Vault, each its own vault id)."""
        return "vault" in str(type_name).lower()

    # ---- the plan: every read and every check before any write
    def plan(self):
        self.resolve()
        if self.problems:
            return []
        tpls = self.all("job_templates/")
        renamed = self.p.get("renamed") or {}
        if self.check and renamed:            # 1.79.19: a Check run of the renames - each read under its new name
            tpls = [dict(t, name=renamed[t.get("name")]) if t.get("organization") == self.org_id and t.get("name") in renamed
                    else t for t in tpls]
        by_name = {}
        for t in tpls:
            by_name.setdefault(t.get("name"), []).append(t)
        self.catalog_names = set(e["name"] for e in self.p["catalog"])
        self.left_off = {}
        steps = []
        self.tid = {}                          # name -> the template's id (ours, in the organization)
        for t in tpls:
            if t.get("organization") == self.org_id:
                self.tid.setdefault(t.get("name"), int(t["id"]))
        to_label = []                          # the catalog entries to label (label_portal)
        self.ours_row = {}                     # 1.107.0: name -> the template of that name in the organization
        steps_of = self.workflow_steps()       # 1.92.0 review: the steps of every workflow we make - never labelled
        self.step_skip = {}
        for e in self.p["catalog"]:
            if not self.wanted(e):
                continue
            name = e["name"]
            ours = [t for t in by_name.get(name) or [] if t.get("organization") == self.org_id]
            if len(ours) == 1:
                self.ours_row[name] = ours[0]
            if len(ours) > 1:                  # Codex: a problem before any write - never one of them picked
                self.problems.append("AAP has %d job templates named %r in the organization: rename one" % (len(ours), name))
                continue
            # 1.83.0 (Codex): never a step of the catalog's workflows (the checks, the gate, the update) - the workflow carries
            # the portal's label, its steps are not jobs to launch alone
            if (self.p["label_portal"] == "all" or (self.p["label_portal"] == "portal-actions" and e.get("portal"))
                    or (ours and "labels" in self.matched(e))) and name not in self.portal_needed():
                # 1.92.0 review: a step of a workflow (the portal's own, the catalog's) or a playbook that only runs a workflow's
                # plan is never labelled - the portal shows the workflow, not its parts
                if name in steps_of or str(e.get("playbook") or "") in NEVER_ALONE:
                    self.step_skip[name] = ("not labelled portal: a step of %s - the workflow carries the label" % ", ".join(steps_of[name])
                                            if name in steps_of else "not labelled portal: it runs %s, which does nothing without a workflow's plan" % e.get("playbook"))
                else:
                    to_label.append(e)
            if e.get("made_by") and ours and name in (self.p.get("needed") or []):
                # 1.83.0 (Codex P1, final pass): a step there under its catalog name is checked as one under another name -
                # a "Workflow gate - has a plan" that runs the update would get the plan before the approval
                why = self.step_why(ours[0], e)
                if why:
                    self.refused.append(name)
                    self.note(name, "skipped", why="%s; the workflows that need it are not made" % why)
                    continue
            if e.get("made_by") and not ours and name in (self.p.get("needed") or []):
                # 1.83.0: a step of a catalog workflow AAP has not under its name: the site's own template of its playbook
                # is used (the director: never a second update or gate), else it is made here (below)
                if self.use_instead(tpls, e):
                    continue
            elif e.get("made_by"):
                other = [] if ours else self.same_playbook(tpls, e)
                self.note(name, "left", why=("made by %s (its own job), and it is there" % e["made_by"]) if ours else
                          "NOT IN AAP - %s makes it, with the workflow that runs it: run that job%s"
                          % (e["made_by"], ("; %s runs its playbook" % ", ".join(repr(t["name"]) for t in other[:3])) if other else ""))
                self.hint(e, other)
                continue
            missing = [k for k in e.get("credentials") or [] if k not in self.creds]
            if missing:
                self.note(name, "skipped", why="credential kind %s not mapped (aap_templates_credentials)" % ", ".join(missing))
                continue
            req = list(e.get("credentials") or [])
            kinds, want_creds, left = list(req), [], []
            for k in req:
                for c in self.creds[k]:
                    if c not in want_creds:
                        want_creds.append(c)
            # an optional kind whose credential is of a type the template already has (a Windows Machine beside the
            # Linux one): left off - a template takes one of a type (Vault aside)
            for k in [k for k in e.get("optional_credentials") or [] if k in self.creds]:
                fit = [c for c in self.creds[k] if c not in want_creds
                       and (self.multi(c[3]) or not any(w[2] == c[2] for w in want_creds))]
                lost = [c for c in self.creds[k] if c not in want_creds and c not in fit]
                if lost:
                    left.append("%s (%s) - the template has a %s credential already, and takes one"
                                % (", ".join(c[1] for c in lost), k, "/".join(sorted(set(str(c[3]) for c in lost)))))
                if fit:
                    kinds.append(k)
                    want_creds.extend(fit)
            if left:
                self.left_off[name] = "not attached: %s" % "; ".join(left)
            # Codex: two of one type on one template (two Machine credentials) - AAP takes one; said, never half written
            by_type = {}
            for c in want_creds:
                by_type.setdefault((c[2], c[3]), []).append(c)
            clash = [(tn, cs) for (ti, tn), cs in by_type.items() if len(cs) > 1 and not self.multi(tn)]
            if clash:
                self.note(name, "skipped", why="two credentials of one type: %s - a template takes one; map one (aap_templates_credentials)"
                          % "; ".join("%s: %s" % (tn, ", ".join(c[1] for c in cs)) for tn, cs in clash))
                continue
            ee = self.ees.get(e.get("ee") or "all")
            fields = {"playbook": e["playbook"], "project": self.project, "ask_limit_on_launch": bool(e.get("ask_limit")),
                      "ask_variables_on_launch": bool(e.get("ask_variables")), "ask_inventory_on_launch": bool(e.get("ask_inventory")),
                      "ask_job_type_on_launch": bool(e.get("ask_job_type"))}
            if e.get("ask_diff_mode"):          # 1.107.0: Show changes (STIG Manager - deploy's dry run) - set only where the catalog asks
                fields["ask_diff_mode_on_launch"] = True
            # 1.107.0 (review): its Job type - a dry-run template is Check itself (never a node's prompt), every other one Run
            # (a workflow's step after its approval sets none: the template's own is what runs)
            fields["job_type"] = e.get("job_type") or "run"
            if e.get("diff_mode"):
                fields["diff_mode"] = True
            # an EE kind not mapped: the project's (none set on the template) - one set is said, cleared only with overwrite
            fields["execution_environment"] = ee[0] if ee else None
            same = by_name.get(name) or []
            if same and not ours:
                self.note(name, "skipped", why="the name is used by a template of another organization")
                continue
            if not ours:
                other = self.same_playbook(tpls, e)
                old = [t["name"] for t in other if t["name"] in (e.get("replaces") or [])]
                if old and not self.p["create_alias"]:
                    # 1.83.0: the template this one replaces runs its playbook: renamed by a person, never a second one
                    self.note(name, "rename", why="rename %r to %r when ready - nothing renames it (aap_templates_create_alias: "
                              "true makes a new one beside it)" % (old[0], name))
                    self.renamed.append(old[0])
                    continue
                if other and not self.p["create_alias"]:
                    # never "the portal looks for it": its step templates are the portal setup job's (made_by), with
                    # other names - a workflow of the site's own may name the catalog's, hence the alias
                    self.note(name, "other name", why="%s runs its playbook - not made twice; aap_templates_create_alias: true "
                              "makes it under this name too" % ", ".join(repr(t["name"]) for t in other[:3]))
                    self.hint(e, other)
                    continue
                if not self.p.get("create_missing", True):     # 1.79.19: renames and match only
                    self.note(name, "missing", why="not made (aap_templates_create_missing: false)")
                    continue
                if not self.inventory:          # Codex: needed only to make one - said before any write
                    self.problems.append("aap_templates_inventory is not set: %r would be made, and a template is made with an "
                                         "inventory" % name)
                    continue
                text, why = self.read_variables(e)          # Codex: read now, never half way through the writes
                if why:
                    self.problems.append("%r: %s" % (name, why))
                    continue
                # 1.107.0 (review): a dry run is made from the template it previews - its Variables (with the catalog's fixed
                # settings set) and its inventory - so it shows what that one will do (each run checks it again)
                base = [t for t in by_name.get(e.get("copy_of")) or [] if t.get("organization") == self.org_id
                        and t.get("playbook") == e["playbook"] and t.get("project") == self.project] if e.get("copy_of") else []
                if len(base) == 1:
                    text = set_vars(base[0].get("extra_vars"), e.get("extra_vars") or {})
                    if base[0].get("inventory"):
                        fields = dict(fields, inventory=int(base[0]["inventory"]))
                steps.append(("create", e, fields, want_creds, kinds, text))
                continue
            t = ours[0]
            if self.p.get("match") and self.inventory:      # 1.79.19: the inventory only with aap_templates_match
                fields["inventory"] = self.inventory
            m = self.matched(e)
            diff, mdiff = {}, {}                  # mdiff: what aap_templates_match brings right without overwrite
            for k, v in fields.items():
                have_v = (t.get(k) or "run") if k == "job_type" else t.get(k)
                if have_v != v and (k != "inventory" or "inventory" in m):
                    diff[SHOW[k]] = [self.show(k, t.get(k)), self.show(k, v)]
                    if any(k in MATCH[w] for w in m):
                        mdiff[SHOW[k]] = diff[SHOW[k]]
            have = [(int(c["id"]), c.get("name"), c.get("credential_type")) for c in self.all("job_templates/%d/credentials/" % int(t["id"]))]
            cfix, mcfix, kept = [], [], []         # per type: (to attach, to take off) - a type compared as a set (Codex)
            for (ti, tn), cs in sorted(by_type.items(), key=lambda x: str(x[0])):
                want_ids = set(c[0] for c in cs)
                add = [c for c in cs if not any(h[0] == c[0] for h in have)]
                if not add:
                    continue
                drop = [] if self.multi(tn) else [h for h in have if h[2] == ti and h[0] not in want_ids]
                cfix.append((add, drop))
                kind = next(k for k in kinds if any(c in self.creds[k] for c in cs))
                diff["credential %s" % kind] = [", ".join(h[1] for h in drop) or "none", ", ".join(c[1] for c in add)]
                if "credentials" in m:
                    # 1.79.19: match only adds - one of that type there (a template takes one) stays, said
                    if drop:
                        kept.append("credential %s: %s kept (a %s credential is there; aap_templates_overwrite: true puts %s instead)"
                                    % (kind, ", ".join(h[1] for h in drop), tn, ", ".join(c[1] for c in add)))
                    else:
                        mcfix.append((add, []))
                        mdiff["credential %s" % kind] = diff["credential %s" % kind]
            if name in (self.p.get("needed") or []) and (SHOW["playbook"] in diff or SHOW["project"] in diff) and not self.p["overwrite"]:
                self.refused.append(name)        # 1.83.0 (Codex P1, final pass): a step that runs another playbook - never wired
            # 1.83.1 (P2-1): its fixed Variables (a workflow step's: the plan var, the drift scan's settings) - one missing
            # or changed differs, never "already right"
            fixed = e.get("extra_vars") or {}
            have_vars = vars_of(t.get("extra_vars")) if fixed else {}
            wrong = [k for k in sorted(fixed) if have_vars.get(k) != _flat(fixed[k])]
            for k in wrong:
                diff["variable %s" % k] = [have_vars[k] if k in have_vars else "none", _flat(fixed[k])]
            if not diff:
                self.note(name, "right")
                continue
            if not self.p["overwrite"] and (mdiff or kept):
                # what match leaves: the other fields that differ (said), and a credential kept (said on its own line)
                kept_keys = set(x.split(":", 1)[0] for x in kept)
                rest = sorted(k for k in diff if k not in mdiff and k not in kept_keys)
                also = ["differs too: %s - aap_templates_overwrite: true brings it right" % ", ".join(rest)] if rest else []
                also += kept
                if also:
                    self.left_off[name] = "; ".join(filter(None, [self.left_off.get(name)] + also))
                if mdiff:
                    steps.append(("fix", e, dict((k, v) for k, v in fields.items() if SHOW[k] in mdiff), mcfix, int(t["id"]), mdiff, True))
                    continue
            fix = dict((k, v) for k, v in fields.items() if SHOW[k] in diff)
            if wrong:
                fix["extra_vars"] = set_vars(t.get("extra_vars"), fixed)
            steps.append(("fix", e, fix, cfix, int(t["id"]), diff))
        # the playbooks to set: in the project as AAP has it synced (Codex) - a missing one stops before any write
        need = sorted(set(st[2]["playbook"] for st in steps if "playbook" in st[2]))
        if need and not self.problems:
            code, d = self.api.call("GET", "projects/%d/playbooks/" % self.project)
            have = d.get("results") if isinstance(d, dict) else None            # AAP's bare list (site_aap wraps it)
            if code != 200 or not isinstance(have, list):
                self.problems.append("the project's playbooks could not be read: %s" % _why(code, d))
            else:
                gone = [x for x in need if x not in have]
                if gone:
                    self.problems.append("not in project %r as AAP has it: %s - sync the project in AAP (or update it), then "
                                         "run this again" % (self.p["project"], ", ".join(gone)))
        # the labels: read now (their ids, each template's own) - only the writes are left for later (Codex)
        self.label_plan = {}
        self.stale_labels = {}                   # name -> the lines naming its old portal-area: labels (never taken off)
        if (self.p["label_portal"] not in ("", "none") or to_label) and not self.problems:
            names = sorted(set(n for e in to_label for n in self.labels_for(e)))
            self.label_id = {}
            for n in names:
                rows = [r for r in self.all("labels/?name=%s" % _q(n)) if r.get("name") == n and r.get("organization") == self.org_id]
                self.label_id[n] = int(rows[0]["id"]) if rows else None
            made = set(st[1]["name"] for st in steps if st[0] == "create")
            for e in to_label:
                tid = self.tid.get(e["name"])
                if tid is None and e["name"] not in made:
                    continue                     # not there and not made: nothing to label
                have = set(r.get("name") for r in self.all("job_templates/%d/labels/" % tid)) if tid else set()
                missing = [n for n in self.labels_for(e) if n not in have]
                if missing:
                    self.label_plan[e["name"]] = missing
                stale = stale_area_labels(have, self.p["portal_label_area"], e.get("area"))
                if stale:
                    self.stale_labels[e["name"]] = ["stale portal-area label %s on %s - remove it by hand" % (n, e["name"]) for n in stale]
        return steps

    def step_why(self, t, e):
        """1.83.0 (Codex P1, final pass): what keeps a template from being a catalog workflow's step - '' when it may be one.
        It runs the step's playbook from this site's project; a step the catalog runs without a Limit prompt does not ask
        for one (the workflow's Limit would reach it); a step given Variables asks for them."""
        if t.get("playbook") != e["playbook"] or t.get("project") != self.project:
            return "%r runs %s%s - the step must run %s from this project" % (
                t["name"], t.get("playbook") or "no playbook", "" if t.get("project") == self.project else " from another project", e["playbook"])
        if t.get("ask_limit_on_launch") and not e.get("ask_limit"):
            return ("%r asks for a Limit on launch: the workflow's Limit would reach it and it would do nothing - untick Prompt "
                    "on launch for Limit on it" % t["name"])
        if not e.get("ask_limit") and limit_misses_localhost(t.get("limit")):
            return ("%r has its own Limit %r, which leaves out localhost: it would run nothing and end green - empty its Limit"
                    % (t["name"], t.get("limit")))
        if e.get("ask_variables") and not t.get("ask_variables_on_launch"):
            return ("%r does not ask for Variables on launch, and its workflow node gives it Variables - tick Prompt on launch "
                    "for Variables on it" % t["name"])
        return ""

    def use_instead(self, tpls, e):
        """1.83.0: a workflow's step (the gate, STIG Manager - update) not in AAP under its catalog name: the site's own
        template of its playbook used instead - one, or the one aap_templates_step_names names. Said either way; True when
        it is settled here (used, or a problem said), False to make it."""
        name = e["name"]
        step = "update" if e["playbook"].endswith("stigman_update.yml") else "gate" if e["playbook"].endswith("workflow_gate.yml") else "workflow"
        named = (self.p.get("step_names") or {}).get(name)
        if named:
            cands = [t for t in tpls if t.get("name") == named and t.get("organization") == self.org_id]
            if len(cands) != 1:
                self.note(name, "skipped", why="aap_templates_step_names names %r for it, and AAP has %s - the workflows that "
                          "need it are not made" % (named, "none of that name" if not cands else "%d of that name" % len(cands)))
                return True
            # 1.83.0 (Codex P1): the named template must run this step's playbook, from this site's project
            t = cands[0]
            if t.get("playbook") != e["playbook"] or t.get("project") != self.project:
                self.note(name, "skipped", why="aap_templates_step_names names %r for it, but it runs %s%s - the %s step "
                          "must run %s from this project; the workflows that need it are not made" % (
                              named, t.get("playbook") or "no playbook", "" if t.get("project") == self.project else " from another project",
                              step, e["playbook"]))
                return True
        else:
            # this site's project only: a lab project's copy of the playbook (with its own credentials) is never a step here
            cands = [t for t in tpls if t.get("playbook") == e["playbook"] and t.get("organization") == self.org_id
                     and t.get("project") == self.project and t.get("name") != name]
            if not cands:
                return False
            if len(cands) > 1:
                self.note(name, "skipped", why="several templates run %s: %s - name the one for the %s step in "
                          "aap_templates_step_names: {%r: '...'}; the workflows that need it are not made"
                          % (e["playbook"], ", ".join(repr(t["name"]) for t in sorted(cands, key=lambda t: t["name"])), step, name))
                return True
        t = cands[0]
        if not e.get("ask_limit") and limit_misses_localhost(t.get("limit")):
            # 1.107.0 (review): its own Limit leaves out localhost - it would run nothing and end green
            self.note(name, "skipped", why="%r runs %s but its own Limit %r leaves out localhost: it would run nothing and end green - "
                      "empty its Limit, then run this again" % (t["name"], e["playbook"], t.get("limit")))
            return True
        if t.get("ask_limit_on_launch"):
            # 1.83.0 (Codex P1): the workflow's Limit would reach it - its play of localhost would match nothing and end green
            # with nothing done (the gate never asked, the approved plan never written)
            self.note(name, "skipped", why="%r runs %s but asks for a Limit on launch: the workflow's Limit would reach it and "
                      "it would do nothing - untick Prompt on launch for Limit on it, then run this again" % (t["name"], e["playbook"]))
            return True
        if e.get("ask_variables") and not t.get("ask_variables_on_launch"):
            self.note(name, "skipped", why="%r runs %s but does not ask for Variables on launch, and its workflow node gives it "
                      "Variables - tick Prompt on launch for Variables on it, then run this again" % (t["name"], e["playbook"]))
            return True
        self.uses[name] = t["name"]
        self.note(name, "uses", why="uses %r (%s) for the %s step" % (t["name"], e["playbook"], step))
        return True

    def same_playbook(self, tpls, e):
        """The site's own templates (the organization, the project) that run the catalog template's playbook under another name -
        never another template of the catalog (a second template of one playbook: "ServiceNow health - nodes")."""
        if e.get("extra_vars") or e.get("job_type"):
            return []                # 1.83.0: a template with fixed Variables (1.107.0: or a fixed Job type: a dry run) is a job of its own
        return [t for t in tpls if t.get("playbook") == e["playbook"] and t.get("project") == self.project
                and t.get("organization") == self.org_id and t.get("name") != e["name"] and t.get("name") not in self.catalog_names]

    def hint(self, e, other):
        """A portal action that runs this one template: it may be pointed at the site's own (portal_aap_names, 1.78.1) -
        said as a line to copy; an action of several templates or an approval: only AAP - set up portal actions."""
        if other and e.get("portal_actions"):
            self.results[-1]["hint"] = "# or: portal_aap_names: {%s} (1.78.1+)" % ", ".join(
                "%s: '%s'" % (a, str(other[0]["name"]).replace("'", "''")) for a in e["portal_actions"])

    def show(self, k, v):
        if k == "project":
            if v == self.project:
                return self.p["project"]
            if not hasattr(self, "_projects"):
                self._projects = dict((int(r["id"]), r.get("name")) for r in self.all("projects/"))
            return repr(self._projects.get(v, "project %s" % v)) if v else "none"
        if k == "execution_environment":
            for kind, (i, n) in self.ees.items():
                if v == i:
                    return n
            return ("execution environment %s" % v) if v else "the project's"
        if k == "inventory":
            if v == self.inventory:
                return self.p["inventory"]
            if not hasattr(self, "_inventories"):
                self._inventories = dict((int(r["id"]), r.get("name")) for r in self.all("inventories/"))
            return repr(self._inventories.get(v, "inventory %s" % v)) if v else "none"
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v

    def read_variables(self, e):
        """A new template's Variables -> (text, why not): its file with every setting switched off, and its fixed settings.
        A file named and not readable is a problem - Variables are set only when a template is made."""
        text = ""
        vf = e.get("variables_file") or ""
        if vf and self.p["variables_dir"]:
            path = os.path.join(self.p["variables_dir"], vf)
            try:
                with open(path, encoding="utf-8") as f:
                    text = off(f.read())
            except (OSError, UnicodeError) as ex:
                return "", "its Variables file %s could not be read (%s)" % (vf, type(ex).__name__)
        fixed = e.get("extra_vars") or {}
        if fixed:
            text = "---\n" + "".join("%s: %s\n" % (k, json.dumps(v)) for k, v in sorted(fixed.items())) + re.sub(r"(?m)^---\n", "", text)
        return text, ""

    def labels_for(self, e):
        out = [self.p["portal_label"]] if self.p["portal_label"] else []
        if e.get("area"):
            out.append("%s%s" % (self.p["portal_label_area"], e["area"]))
        return out

    def row_of(self, name):
        r = next((x for x in self.results if x["name"] == name), None)
        return r if r is not None else self.note(name, "right")

    def label_all(self):
        """The portal's labels the templates lack: added one by one, each said as it is done (Codex) - none taken off."""
        for name, missing in sorted(self.label_plan.items()):
            r = self.row_of(name)
            if self.check:
                r["labels"] = list(missing)
                continue
            tid = self.tid.get(name)
            if tid is None:
                continue                         # it was to be made and was not (a failure before it, said)
            r["labels"] = []
            for n in missing:
                if self.label_id.get(n) is None:
                    self.label_id[n] = int(self.write("POST", "labels/", {"name": n, "organization": self.org_id})["id"])
                self.write("POST", "job_templates/%d/labels/" % tid, {"id": self.label_id[n]})
                r["labels"].append(n)

    def surveys(self, steps):
        """1.107.0: the survey questions the catalog's workflows give their steps (Health check's health_checks ...): the
        missing ones added - never one changed or taken off - and the survey turned on. Check says which it would add."""
        made = set(st[1]["name"] for st in steps if st[0] == "create")
        self.survey_adds = {}
        for e in self.p["catalog"]:
            if not e.get("survey") or not self.wanted(e):
                continue
            name = e["name"]
            qs = [survey_question(q) for q in e["survey"]]
            tid = self.tid.get(name)
            if tid is None:
                if self.check and name in made:          # made in this run: it gets them all
                    self.survey_adds[name] = [q["variable"] for q in qs]
                    self.row_of(name)["survey"] = "would add its survey questions: %s" % ", ".join(self.survey_adds[name])
                continue
            row0 = (getattr(self, "ours_row", {}) or {}).get(name)
            # 1.107.0 (review): the template as it is after this run - only a fix that writes its playbook or project changes
            # what it is (a partial match of its prompts never makes another project's template the catalog's)
            eff = dict(row0 or {})
            for st in steps:
                if st[0] == "fix" and st[1]["name"] == name and (self.p["overwrite"] or (len(st) > 6 and st[6])):
                    eff.update(dict((k, v) for k, v in (st[2] or {}).items() if k in ("playbook", "project")))
            if name not in made and (row0 is None or eff.get("playbook") != e["playbook"] or eff.get("project") != self.project
                                     or name in self.refused):
                # 1.107.0 (review): never the catalog's questions on a template of that name that is not the catalog's job
                self.row_of(name)["survey"] = ("no survey questions written: it runs %s%s, not %s from this project" % (
                    eff.get("playbook") or "another playbook",
                    "" if eff.get("project") == self.project else " from another project", e["playbook"]))
                continue
            try:
                path = "job_templates/%d/" % tid
                code, have = self.api.call("GET", path + "survey_spec/")
                if code != 200:
                    raise RuntimeError("the survey of %r could not be read (HTTP %d): %s" % (name, code, _why(code, have)))
                have = dict(have or {})
                got = [str(q.get("variable")) for q in have.get("spec") or []]
                add = [q for q in qs if q["variable"] not in got]
                code, row = self.api.call("GET", path)
                enabled = bool((row or {}).get("survey_enabled")) if code == 200 else False
                # 1.107.0 (review): a survey there that its owner switched off stays off without overwrite (said); a new one
                # (none there before) is turned on with the questions it gets
                turn_on = not enabled and (not have.get("spec") or self.p["overwrite"])
                left_off = not enabled and not turn_on
                if not add and (enabled or left_off):
                    if left_off:
                        self.row_of(name)["survey"] = ("its survey is off: left off - the workflows that give its steps answers are "
                                                       "skipped until it is on (aap_templates_overwrite: true turns it on)")
                    continue
                r = self.row_of(name)
                words = ", ".join(q["variable"] for q in add)
                tail = ("; would turn its survey on" if self.check else "; its survey turned on") if turn_on else (
                    "; its survey is off: left off (aap_templates_overwrite: true turns it on)" if left_off else "")
                if self.check:                  # its questions as they will be: there (on) and added
                    if not left_off:
                        self.survey_adds[name] = got + [q["variable"] for q in add]
                    r["survey"] = ("would add survey question(s): %s" % words if add else "") + tail
                    continue
                if add:
                    self.write("POST", path + "survey_spec/", {"name": have.get("name") or "", "description": have.get("description") or "",
                                                               "spec": list(have.get("spec") or []) + add})
                if turn_on:
                    self.write("PATCH", path, {"survey_enabled": True})
                self.wrote = True
                self.survey_adds[name] = [q["variable"] for q in add] if not left_off else []
                r["survey"] = ("survey question(s) added: %s" % words if add else "") + tail
            except RuntimeError as ex:      # 1.107.0 (review): said for this template; the others go on
                self.row_of(name)["survey"] = "FAILED - its survey questions: %s" % str(ex)[:300]
                self.problems.append("%r: its survey questions were not added: %s" % (name, str(ex)[:300]))

    def run(self):
        try:
            self.run_all()
        finally:
            for r in self.results:
                if r["name"] in getattr(self, "left_off", {}):
                    r.setdefault("also", self.left_off[r["name"]])

    def run_all(self):
        self.wrote = False
        steps = self.plan()
        if self.problems:
            return
        self.steps(steps)
        if self.problems:
            return
        try:
            self.surveys(steps)
        except RuntimeError as ex:
            self.problems.append("the survey questions: %s (the ones added before it are listed)" % ex)
        if self.problems or not getattr(self, "label_plan", None):
            return
        try:
            self.label_all()
        except RuntimeError as ex:
            self.problems.append("the portal labels: %s (the labels added before it are listed)" % ex)

    def steps(self, steps):
        for st in steps:
            kind, e = st[0], st[1]
            name = e["name"]
            if kind == "fix" and not self.p["overwrite"] and not (len(st) > 6 and st[6]):
                self.note(name, "differs", st[5], "aap_templates_overwrite: true brings it right")
                continue
            if self.check:
                if kind == "create":
                    d = dict((SHOW[k], [None, self.show(k, v)]) for k, v in st[2].items() if not k.startswith("ask_"))
                    d["credentials"] = [None, ", ".join(c[1] for c in st[3]) or "none"]
                    d["prompts"] = [None, ", ".join(SHOW[k][len("prompt for "):] for k, v in st[2].items() if k.startswith("ask_") and v) or "none"]
                    self.note(name, "create", d, "would make it")
                else:
                    self.note(name, "fix", st[5], "would bring it right")
                continue
            done = []                            # Codex: each write said as it is done - a failure says what was done before it
            try:
                if kind == "create":
                    body = dict(dict(job_type="run"), **st[2])
                    body.update(name=name, organization=self.org_id, inventory=st[2].get("inventory") or self.inventory, description=DESC,
                                extra_vars=st[5])
                    tid = int(self.write("POST", "job_templates/", body)["id"])
                    self.tid[name] = tid
                    done.append("made it")
                    for c in st[3]:
                        self.write("POST", "job_templates/%d/credentials/" % tid, {"id": c[0]})
                        done.append("attached %s" % c[1])
                    self.note(name, "create", why="made")
                else:
                    tid = st[4]
                    if st[2]:
                        self.write("PATCH", "job_templates/%d/" % tid, st[2])
                        done.append("changed %s" % ", ".join(SHOW[k] for k in sorted(st[2])))
                    for add, drop in st[3]:
                        for h in drop:           # one of that type the catalog does not want (another type: never touched)
                            self.write("POST", "job_templates/%d/credentials/" % tid, {"id": h[0], "disassociate": True})
                            done.append("took off %s" % h[1])
                        for c in add:
                            self.write("POST", "job_templates/%d/credentials/" % tid, {"id": c[0]})
                            done.append("attached %s" % c[1])
                    self.note(name, "fix", st[5], "brought right")
            except RuntimeError as ex:
                self.note(name, "failed", why="%s%s" % (ex, (" - done before it: " + "; ".join(done)) if done else ""))
                self.problems.append("stopped at %r: %s (what was done before it is listed)" % (name, ex))
                return


def main():
    module = AnsibleModule(
        argument_spec=dict(catalog=dict(type="list", elements="dict", required=True), organization=dict(type="str", required=True),
                           project=dict(type="str", required=True), inventory=dict(type="str", default=""), inventory_organization=dict(type="str", default=""),
                           ee=dict(type="dict", default={}), credentials=dict(type="dict", default={}),
                           only=dict(type="list", elements="str", default=[]), overwrite=dict(type="bool", default=False),
                           create_alias=dict(type="bool", default=False),
                           label_portal=dict(type="str", default="none", choices=["none", "portal-actions", "all"]),
                           portal_label=dict(type="str", default="portal"), portal_label_area=dict(type="str", default="portal-area:"),
                           variables_dir=dict(type="str", default=""),
                           match=dict(type="list", elements="str", default=[]), match_skip_areas=dict(type="list", elements="str", default=[]),
                           renamed=dict(type="dict", default={}), create_missing=dict(type="bool", default=True),
                           needed=dict(type="list", elements="str", default=[]), step_names=dict(type="dict", default={}),
                           portal_needed=dict(type="list", elements="str", default=None),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_templates_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], problems=[why])
    s = Templates(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as ex:
        s.problems.append(str(ex))
    order = dict((e.get("name"), i) for i, e in enumerate(p["catalog"]))
    s.results.sort(key=lambda r: order.get(r["name"], 10 ** 6))
    changed = any(r["action"] in ("create", "fix") or r.get("labels") for r in s.results) or getattr(s, "wrote", False)
    for r in s.results:                                  # 1.92.0 review: a step not labelled - said on its own line
        why = (getattr(s, "step_skip", {}) or {}).get(r.get("name"))
        if why:
            r["also"] = (r["also"] + "; " + why) if r.get("also") else why
    module.exit_json(changed=changed, results=s.results, problems=s.problems, uses=s.uses, renamed=s.renamed, refused=s.refused,
                     survey_adds=getattr(s, "survey_adds", {}),
                     stale_labels=[x for _, lines in sorted((getattr(s, "stale_labels", None) or {}).items()) for x in lines])


if __name__ == "__main__":
    main()
