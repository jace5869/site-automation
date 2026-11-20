#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates (playbooks/aap_templates_setup.yml): the job templates of aap/job_templates.yml (the catalog,
one per template of docs/JOB_TEMPLATES.md), made in AAP through its own API, or checked against it. A missing one is made;
one that differs is listed field by field and brought right only with overwrite. Never deletes; never touches a
template's schedules, survey or Variables after it is made, nor a credential of a type the catalog does not name. A
template running the same playbook under another name is listed, not made twice. Everything is read and checked first;
in check mode nothing is written. Only a credential's id, name and type are read out. Standard library only."""
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
  variables_dir: {description: "The folder of the catalog's Variables files (the repository's root).", type: str, default: ''}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each template: {name, action (create|fix|right|differs|other name|skipped|left|failed), diff ({field:
  [has, should be]}), why, labels (the portal labels it gets, or would)}.", type: list}
problems: {description: "What stopped the job before any write: a name not found, AAP not reached.", type: list}
'''

import fnmatch
import json
import os
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it makes job templates)')
DESC = "Made by AAP - set up job templates (aap/job_templates.yml)"
SHOW = {"playbook": "playbook", "project": "project", "execution_environment": "execution environment",
        "ask_limit_on_launch": "prompt for Limit", "ask_variables_on_launch": "prompt for Variables",
        "ask_inventory_on_launch": "prompt for Inventory", "ask_job_type_on_launch": "prompt for Job Type"}


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


class Templates(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
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
        for e in self.p["catalog"]:
            if not self.wanted(e):
                continue
            name = e["name"]
            ours = [t for t in by_name.get(name) or [] if t.get("organization") == self.org_id]
            if len(ours) > 1:                  # Codex: a problem before any write - never one of them picked
                self.problems.append("AAP has %d job templates named %r in the organization: rename one" % (len(ours), name))
                continue
            if self.p["label_portal"] == "all" or (self.p["label_portal"] == "portal-actions" and e.get("portal")):
                to_label.append(e)
            if e.get("made_by"):
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
            # an EE kind not mapped: the project's (none set on the template) - one set is said, cleared only with overwrite
            fields["execution_environment"] = ee[0] if ee else None
            same = by_name.get(name) or []
            if same and not ours:
                self.note(name, "skipped", why="the name is used by a template of another organization")
                continue
            if not ours:
                other = self.same_playbook(tpls, e)
                if other and not self.p["create_alias"]:
                    # never "the portal looks for it": its step templates are the portal setup job's (made_by), with
                    # other names - a workflow of the site's own may name the catalog's, hence the alias
                    self.note(name, "other name", why="%s runs its playbook - not made twice; aap_templates_create_alias: true "
                              "makes it under this name too" % ", ".join(repr(t["name"]) for t in other[:3]))
                    self.hint(e, other)
                    continue
                if not self.inventory:          # Codex: needed only to make one - said before any write
                    self.problems.append("aap_templates_inventory is not set: %r would be made, and a template is made with an "
                                         "inventory" % name)
                    continue
                text, why = self.read_variables(e)          # Codex: read now, never half way through the writes
                if why:
                    self.problems.append("%r: %s" % (name, why))
                    continue
                steps.append(("create", e, fields, want_creds, kinds, text))
                continue
            t = ours[0]
            diff = {}
            for k, v in fields.items():
                if t.get(k) != v:
                    diff[SHOW[k]] = [self.show(k, t.get(k)), self.show(k, v)]
            have = [(int(c["id"]), c.get("name"), c.get("credential_type")) for c in self.all("job_templates/%d/credentials/" % int(t["id"]))]
            cfix = []                             # per type: (to attach, to take off) - a type compared as a set (Codex)
            for (ti, tn), cs in sorted(by_type.items(), key=lambda x: str(x[0])):
                want_ids = set(c[0] for c in cs)
                add = [c for c in cs if not any(h[0] == c[0] for h in have)]
                if not add:
                    continue
                drop = [] if self.multi(tn) else [h for h in have if h[2] == ti and h[0] not in want_ids]
                cfix.append((add, drop))
                kind = next(k for k in kinds if any(c in self.creds[k] for c in cs))
                diff["credential %s" % kind] = [", ".join(h[1] for h in drop) or "none", ", ".join(c[1] for c in add)]
            if not diff:
                self.note(name, "right")
                continue
            steps.append(("fix", e, dict((k, v) for k, v in fields.items() if SHOW[k] in diff), cfix, int(t["id"]), diff))
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
        if self.p["label_portal"] not in ("", "none") and not self.problems:
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
        return steps

    def same_playbook(self, tpls, e):
        """The site's own templates (the organization, the project) that run the catalog template's playbook under another name -
        never another template of the catalog (a second template of one playbook: "ServiceNow health - nodes")."""
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
        if self.problems or self.p["label_portal"] in ("", "none"):
            return
        try:
            self.label_all()
        except RuntimeError as ex:
            self.problems.append("the portal labels: %s (the labels added before it are listed)" % ex)

    def steps(self, steps):
        for st in steps:
            kind, e = st[0], st[1]
            name = e["name"]
            if kind == "fix" and not self.p["overwrite"]:
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
                    body = dict(st[2], name=name, organization=self.org_id, inventory=self.inventory, job_type="run", description=DESC,
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
    module.exit_json(changed=changed, results=s.results, problems=s.problems)


if __name__ == "__main__":
    main()
