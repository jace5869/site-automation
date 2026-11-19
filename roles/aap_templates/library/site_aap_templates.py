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
  ee: {description: "{all: name, windows: name, network: name} - an execution environment per kind ('' = the project's).", type: dict, default: {}}
  credentials: {description: "{kind: credential name} (a list of names for several).", type: dict, default: {}}
  only: {description: "Only the templates whose name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  overwrite: {description: "Bring a template that differs right (only the fields this job sets).", type: bool, default: false}
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
        "ask_inventory_on_launch": "prompt for Inventory"}


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
        rows = [r for r in self.all("%s/?name=%s" % (kind, _q(name))) if r.get("name") == name]
        if org and self.org_id is not None:
            mine = [r for r in rows if r.get("organization") in (self.org_id, None)]
            rows = mine or rows
        if len(rows) != 1:
            self.problems.append("%s %r: %s" % (what, name, "not found" if not rows else "%d match - rename one in AAP" % len(rows)))
            return None
        return rows[0]

    def note(self, name, action, diff=None, why=""):
        self.results.append({"name": name, "action": action, "diff": diff or {}, "why": why})

    def write(self, method, path, body=None):
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: %s" % (method, path.split("?")[0], _why(code, d)))
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
            inv = self.one("inventories", p["inventory"], "inventory (aap_templates_inventory)")
            self.inventory = int(inv["id"]) if inv else None
        self.ees = {}
        for kind, name in sorted((p["ee"] or {}).items()):
            if name:
                e = self.one("execution_environments", str(name), "execution environment %s (aap_templates_ee)" % kind)
                if e:
                    self.ees[kind] = (int(e["id"]), str(name))
        self.creds = {}                       # kind -> [(id, name, type)]
        for kind, names in sorted((p["credentials"] or {}).items()):
            names = [names] if isinstance(names, str) else list(names or [])
            got = []
            for n in names:
                c = self.one("credentials", str(n), "credential %s (aap_templates_credentials)" % kind)
                if c:
                    got.append((int(c["id"]), str(n), c.get("credential_type")))
            if got:
                self.creds[kind] = got

    def wanted(self, e):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(e["name"].casefold(), x) for x in pats)

    # ---- one template: made, checked, or left
    def plan(self):
        self.resolve()
        if self.problems:
            return []
        if not self.inventory:
            self.problems.append("aap_templates_inventory is not set: a job template is made with an inventory")
            return []
        tpls = self.all("job_templates/")
        by_name = {}
        for t in tpls:
            by_name.setdefault(t.get("name"), []).append(t)
        steps = []
        self.tid = {}                          # name -> the template's id (ours, in the organization)
        for t in tpls:
            if t.get("organization") == self.org_id:
                self.tid.setdefault(t.get("name"), int(t["id"]))
        self.to_label = []                     # the catalog entries to label (label_portal)
        for e in self.p["catalog"]:
            if not self.wanted(e):
                continue
            name = e["name"]
            if self.p["label_portal"] == "all" or (self.p["label_portal"] == "portal-actions" and e.get("portal")):
                self.to_label.append(e)
            if e.get("made_by"):
                self.note(name, "left", why="made by %s (its own job) - not here" % e["made_by"])
                continue
            missing = [k for k in e.get("credentials") or [] if k not in self.creds]
            if missing:
                self.note(name, "skipped", why="credential kind %s not mapped (aap_templates_credentials)" % ", ".join(missing))
                continue
            kinds = list(e.get("credentials") or []) + [k for k in e.get("optional_credentials") or [] if k in self.creds]
            want_creds = [c for k in kinds for c in self.creds[k]]
            ee = self.ees.get(e.get("ee") or "all")
            fields = {"playbook": e["playbook"], "project": self.project, "ask_limit_on_launch": bool(e.get("ask_limit")),
                      "ask_variables_on_launch": bool(e.get("ask_variables")), "ask_inventory_on_launch": bool(e.get("ask_inventory"))}
            if ee:
                fields["execution_environment"] = ee[0]
            same = by_name.get(name) or []
            ours = [t for t in same if t.get("organization") == self.org_id]
            if same and not ours:
                self.note(name, "skipped", why="the name is used by a template of another organization")
                continue
            if len(ours) > 1:
                self.note(name, "skipped", why="AAP has %d templates of that name: rename one" % len(ours))
                continue
            if not ours:
                other = [t for t in tpls if t.get("playbook") == e["playbook"] and t.get("project") == self.project
                         and t.get("organization") == self.org_id and t.get("name") != name]
                if other:
                    self.note(name, "other name", why="%s runs its playbook - not made twice"
                              % ", ".join(repr(t["name"]) for t in other[:3]))
                    continue
                steps.append(("create", e, fields, want_creds, kinds))
                continue
            t = ours[0]
            diff = {}
            for k, v in fields.items():
                if t.get(k) != v:
                    diff[SHOW[k]] = [self.show(k, t.get(k)), self.show(k, v)]
            have = [(int(c["id"]), c.get("name"), c.get("credential_type")) for c in self.all("job_templates/%d/credentials/" % int(t["id"]))]
            cfix = []                             # (kind, the one there of that type or None, the one wanted)
            for c in want_creds:
                if any(h[0] == c[0] for h in have):
                    continue
                there = [h for h in have if h[2] == c[2]]
                cfix.append((c, there))
                diff["credential %s" % next(k for k in kinds if c in self.creds[k])] = [
                    ", ".join(h[1] for h in there) or "none", c[1]]
            if not diff:
                self.note(name, "right")
                continue
            steps.append(("fix", e, dict((k, v) for k, v in fields.items() if SHOW[k] in diff), cfix, int(t["id"]), diff))
        return steps

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

    def variables(self, e):
        text = ""
        vf = e.get("variables_file") or ""
        if vf and self.p["variables_dir"]:
            path = os.path.join(self.p["variables_dir"], vf)
            if os.path.isfile(path):
                with open(path) as f:
                    text = off(f.read())
        fixed = e.get("extra_vars") or {}
        if fixed:
            text = "---\n" + "".join("%s: %s\n" % (k, json.dumps(v)) for k, v in sorted(fixed.items())) + re.sub(r"(?m)^---\n", "", text)
        return text

    def labels_for(self, e):
        out = [self.p["portal_label"]] if self.p["portal_label"] else []
        if e.get("area"):
            out.append("%s%s" % (self.p["portal_label_area"], e["area"]))
        return out

    def label_ids(self, names):
        """The organization's labels of these names -> {name: id}; one not there is made (not in check mode)."""
        out = {}
        for n in names:
            rows = [r for r in self.all("labels/?name=%s" % _q(n)) if r.get("name") == n and r.get("organization") == self.org_id]
            if rows:
                out[n] = int(rows[0]["id"])
            elif not self.check:
                out[n] = int(self.write("POST", "labels/", {"name": n, "organization": self.org_id})["id"])
        return out

    def label_all(self):
        """The portal's labels on each template to label: the ones it lacks are added (nothing is ever taken off)."""
        done = {}
        for e in self.to_label:
            tid = self.tid.get(e["name"])
            want = self.labels_for(e)
            if not want:
                continue
            have = set()
            if tid:
                have = set(r.get("name") for r in self.all("job_templates/%d/labels/" % tid))
            missing = [n for n in want if n not in have]
            if not missing:
                continue
            if tid is None and not self.check:
                continue                             # not made (skipped, other name, failed): nothing to label
            if not self.check:
                ids = self.label_ids(missing)
                for n in missing:
                    self.write("POST", "job_templates/%d/labels/" % tid, {"id": ids[n]})
            done[e["name"]] = missing
        return done

    def run(self):
        steps = self.plan()
        if self.problems:
            return
        self.steps(steps)
        if self.problems or self.p["label_portal"] in ("", "none"):
            return
        made = set(r["name"] for r in self.results if r["action"] in ("create",))
        there = set(self.tid) | made
        self.to_label = [e for e in self.to_label if e["name"] in there]
        try:
            done = self.label_all()
        except RuntimeError as ex:
            self.problems.append("the portal labels: %s" % ex)
            return
        for r in self.results:
            if r["name"] in done:
                r["labels"] = done[r["name"]]
        for name, lab in done.items():                 # a template labelled with no other change has a row too
            if not any(r["name"] == name for r in self.results):
                self.note(name, "right")
                self.results[-1]["labels"] = lab

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
            try:
                if kind == "create":
                    body = dict(st[2], name=name, organization=self.org_id, inventory=self.inventory, job_type="run", description=DESC,
                                extra_vars=self.variables(e))
                    tid = int(self.write("POST", "job_templates/", body)["id"])
                    self.tid[name] = tid
                    for c in st[3]:
                        self.write("POST", "job_templates/%d/credentials/" % tid, {"id": c[0]})
                    self.note(name, "create", why="made")
                else:
                    tid = st[4]
                    if st[2]:
                        self.write("PATCH", "job_templates/%d/" % tid, st[2])
                    for c, there in st[3]:
                        for h in there:          # the one of that type there is replaced (another type is never touched)
                            self.write("POST", "job_templates/%d/credentials/" % tid, {"id": h[0], "disassociate": True})
                        self.write("POST", "job_templates/%d/credentials/" % tid, {"id": c[0]})
                    self.note(name, "fix", st[5], "brought right")
            except RuntimeError as ex:
                self.note(name, "failed", why=str(ex))
                self.problems.append("stopped at %r: %s (what was done before it is listed)" % (name, ex))
                return


def main():
    module = AnsibleModule(
        argument_spec=dict(catalog=dict(type="list", elements="dict", required=True), organization=dict(type="str", required=True),
                           project=dict(type="str", required=True), inventory=dict(type="str", default=""),
                           ee=dict(type="dict", default={}), credentials=dict(type="dict", default={}),
                           only=dict(type="list", elements="str", default=[]), overwrite=dict(type="bool", default=False),
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
    changed = any(r["action"] in ("create", "fix") or r.get("labels") for r in s.results)
    module.exit_json(changed=changed, results=s.results, problems=s.problems)


if __name__ == "__main__":
    main()
