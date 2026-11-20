#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates, aap_templates_describe (1.79.11): each job template and workflow of the site-automation
project gets a description of five short labeled lines - what it does, what it runs on, what it produces, what it reads
and writes, and who to ask - from the catalog (aap/job_templates.yml: description, and its workflows):

    ** Does: <one sentence>
    ** Targets: <what it runs on>
    ** Produces: <reports, emails, records>
    ** Supports: Run constraint=<read-only or what it changes>; writes <what>
    ** Contact: Owner=<owner>, Developer=<contact>        (only from the site's settings; left out when both are empty)

A template is found by its playbook in the project (the site's own names match too), every template of that playbook; a
playbook of several catalog templates (a second one with its own Variables) by name, then by those Variables. A
workflow by its name, else by the playbooks of its steps. A description is written when it is empty, or ours (it starts
with "** Does:", or is a text this repository wrote: the template's "Made by ..." line, the portal job's own); a
hand-written one is kept unless overwrite; one AAP - set up portal actions wrote (its fingerprint mark) is left to that
job. Nothing found is never a failure: a catalog template not in AAP, a template or
workflow the catalog does not know - each listed. Only a write AAP refuses fails the job. Check mode only reads.
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_describe
short_description: Write the catalog's five-line descriptions on the site-automation job templates and workflows in AAP
options:
  catalog: {description: "aap/job_templates.yml's job_templates (each with description {does, targets, produces, supports}).", type: list, elements: dict, required: true}
  workflows: {description: "aap/job_templates.yml's workflows: [{name, playbooks, description}].", type: list, elements: dict, default: []}
  organization: {description: The organization (its name)., type: str, required: true}
  project: {description: The site-automation project (its name)., type: str, required: true}
  owner: {description: "The Contact line's Owner= (aap_templates_description_owner; '' = none).", type: str, default: ''}
  contact: {description: "The Contact line's Developer= (aap_templates_description_contact; '' = none).", type: str, default: ''}
  ours: {description: "Descriptions this repository wrote before (replaced like one of ours).", type: list, elements: str, default: []}
  overwrite: {description: "Write over a hand-written description too (aap_templates_describe_overwrite).", type: bool, default: false}
  only: {description: "Only the templates whose AAP or catalog name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  vendored: {description: "1.79.11d - ACT's own playbooks (aap/job_templates.yml vendored, each {name, playbook, description}): a template that runs one is described by its playbook; never not in AAP.", type: list, elements: dict, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each template or workflow: {kind (job|workflow), name (in AAP), catalog (its catalog name), action
  (describe|right|kept|left|not in AAP|no description known|failed), was, now (first lines), why}.", type: list}
problems: {description: "The writes AAP refused (the job fails on these only).", type: list}
notes: {description: "What could not be read (AAP not reached, a list not read): a warning - nothing was written then.", type: list}
twice: {description: "1.79.11d - catalog templates the site has more than one template of (the same playbook): one line each.", type: list}
'''

import collections
import fnmatch
import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, described_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user who may change the organization\'s templates)')
MARK = "** Does:"
PORTAL_MARK = "[set up by AAP - set up portal actions, fingerprint "     # roles/aap_portal_setup (MARK_TEXT)


def _q(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def _why(code, d):
    """What AAP refused, by its field names only - never a value it sent back."""
    keys = sorted(str(k) for k in (d or {}) if k != "detail") if isinstance(d, dict) else []
    return "HTTP %d%s" % (code, (" (fields: %s)" % ", ".join(keys)) if keys else "")


def _one_line(v):
    return " ".join(str(v or "").split())


def text_of(desc, owner="", contact=""):
    """The five lines (the Contact line only with an owner or a contact); '' when the catalog gives no Does."""
    return described_text(desc, owner, contact)


def first(text):
    t = str(text or "").strip()
    return (t.splitlines()[0][:120] + (" ..." if len(t.splitlines()) > 1 or len(t.splitlines()[0]) > 120 else "")) if t else "(empty)"


def _vars_of(text):
    """A template's Variables (JSON or YAML text) as a dict - {} when they cannot be read."""
    t = str(text or "").strip()
    if not t:
        return {}
    try:
        d = json.loads(t)
    except ValueError:
        try:
            import yaml
            d = yaml.safe_load(t)
        except Exception:                               # noqa: BLE001 - not readable: no Variables of its own
            return {}
    return d if isinstance(d, dict) else {}


def _vars_have(text, want):
    """A template's Variables carry each of want's keys with its value (as set: a commented line is not set)."""
    have = _vars_of(text)
    return all(k in have and str(have[k]).lower() == str(v).lower() for k, v in (want or {}).items())


class Describe(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.results, self.problems, self.notes, self.wrote = [], [], [], False
        self.twice = []                                   # 1.79.11d: "2 templates run <playbook>: 'A', 'B' - keep one"

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

    def note(self, kind, name, catalog, action, was="", now="", why=""):
        self.results.append({"kind": kind, "name": name, "catalog": catalog, "action": action, "was": was, "now": now, "why": why})

    def wanted(self, *names):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(str(n or "").casefold(), x) for n in names for x in pats)

    def ours(self, have):
        h = str(have or "").strip()
        return not h or h.startswith(MARK) or h in set(str(x).strip() for x in self.p["ours"] or [] if str(x).strip())

    def put(self, kind, t, catalog, text):
        """One template's or workflow's description: written (empty, ours, or overwrite), or said why not."""
        path = "job_templates" if kind == "job" else "workflow_job_templates"
        have = str(t.get("description") or "")
        name = str(t.get("name") or "")
        if PORTAL_MARK in have:                          # AAP - set up portal actions' own: its fingerprint covers it
            self.note(kind, name, catalog, "left", first(have), why="AAP - set up portal actions writes its description "
                      "(roles/site_portal_stack/files/workflows.yml) - a change here would make that job leave it")
            return
        if not text:
            self.note(kind, name, catalog, "no description known", first(have), why="the catalog has none for it")
            return
        if have.strip() == text.strip():
            self.note(kind, name, catalog, "right")
            return
        if not self.ours(have) and not self.p["overwrite"]:
            self.note(kind, name, catalog, "kept", first(have), first(text),
                      "hand-written - aap_templates_describe_overwrite: true writes over it")
            return
        if self.check:
            self.note(kind, name, catalog, "describe", first(have), first(text), "would write it")
            return
        code, d = self.api.call("PATCH", "%s/%d/" % (path, int(t["id"])), {"description": text})
        if code not in (200, 201, 202, 204):
            why = _why(code, d)
            self.note(kind, name, catalog, "failed", first(have), first(text), why)
            self.problems.append("%s %r: the description was not written: %s" % ("job template" if kind == "job" else "workflow", name, why))
            return
        self.wrote = True
        self.note(kind, name, catalog, "describe", first(have), first(text), "written")

    def run(self):
        p = self.p
        orgs = [o for o in self.all("organizations/?name=%s" % _q(p["organization"])) if o.get("name") == p["organization"]]
        if len(orgs) != 1:                               # nothing found is never a failure: said, nothing written
            self.note("job", p["organization"], "", "not in AAP",
                      why="organization %s - nothing described (aap_templates_organization)" % ("not found" if not orgs else "named twice"))
            return
        org = int(orgs[0]["id"])
        projs = [x for x in self.all("projects/?name=%s" % _q(p["project"])) if x.get("name") == p["project"] and x.get("organization") == org]
        if len(projs) != 1:
            self.note("job", p["project"], "", "not in AAP",
                      why="project %s in the organization - nothing described (aap_templates_project)" % ("not found" if not projs else "named twice"))
            return
        proj = int(projs[0]["id"])
        tpls = [t for t in self.all("job_templates/?organization=%d" % org) if t.get("organization") == org]
        pb_of = dict((int(t["id"]), str(t.get("playbook") or "")) for t in tpls if t.get("project") == proj)
        mine = [t for t in tpls if t.get("project") == proj]
        owner, contact = p["owner"], p["contact"]
        by_pb = {}
        for e in list(p["catalog"]) + list(p["vendored"]):         # 1.79.11d: ACT's own playbooks too
            by_pb.setdefault(str(e.get("playbook") or ""), []).append(e)
        found = set()
        runs = {}                                                  # 1.79.11d: catalog entry -> the AAP templates matched to it
        for t in sorted(mine, key=lambda x: str(x.get("name") or "").casefold()):
            pb = str(t.get("playbook") or "")
            es = by_pb.get(pb) or []
            if not es:
                if self.wanted(t.get("name")):
                    self.note("job", str(t.get("name") or ""), "", "no description known", first(t.get("description")),
                              why="its playbook %s is not in the catalog - left as it is" % (pb or "(none)"))
                continue
            # a playbook of several catalog templates: the one of its name, else the one whose Variables it has, else
            # the one with none of its own
            e = next((x for x in es if x["name"] == t.get("name")), None)
            if e is None and len(es) > 1:
                e = next((x for x in es if x.get("extra_vars") and _vars_have(t.get("extra_vars"), x["extra_vars"])), None) \
                    or next((x for x in es if not x.get("extra_vars")), es[0])
            e = e or es[0]
            found.add(e["name"])
            if self.wanted(t.get("name"), e["name"]):
                runs.setdefault(e["name"], (pb, []))[1].append((str(t.get("name") or ""), _vars_of(t.get("extra_vars"))))
                self.put("job", t, e["name"], text_of(e.get("description"), owner, contact))
        # 1.79.11d: two of the site's templates for one catalog template (the same playbook, nothing to tell them apart)
        # "keep one" only when their Variables are the same too: different ones (podman_apps ...) may be different jobs (Codex)
        for name, (pb, ts) in sorted(runs.items()):
            if len(ts) > 1:
                same = all(v == ts[0][1] for _, v in ts)
                self.twice.append("%d templates run %s: %s - %s" % (
                    len(ts), pb, ", ".join("'%s'" % n for n in sorted((n for n, _ in ts), key=str.casefold)),
                    "keep one" if same else "their Variables differ: keep both only if they are different jobs"))
        for e in p["catalog"]:
            if e["name"] not in found and self.wanted(e["name"]):
                self.note("job", e["name"], e["name"], "not in AAP", why="no template in project %r runs %s" % (p["project"], e.get("playbook")))
        # the workflows: by name, else by the playbooks of their steps
        wfs = [w for w in self.all("workflow_job_templates/?organization=%d" % org) if w.get("organization") == org]
        cat = [w for w in p["workflows"] or [] if isinstance(w, dict) and w.get("name")]
        done = set()
        for w in sorted(wfs, key=lambda x: str(x.get("name") or "").casefold()):
            # its steps: the playbooks of its job templates (each as often as it runs), whether every one is of the project,
            # and whether it has an approval
            steps, foreign, approval = collections.Counter(), 0, False
            for n in self.all("workflow_job_template_nodes/?workflow_job_template=%d" % int(w["id"])):
                uj = (n.get("summary_fields") or {}).get("unified_job_template") or {}
                if uj.get("unified_job_type") == "workflow_approval":
                    approval = True
                elif uj.get("unified_job_type") == "job" and uj.get("id") is not None:
                    pb = pb_of.get(int(uj["id"]))
                    if pb is None:
                        foreign += 1                     # a template of another project: not the site-automation workflow
                    else:
                        steps[pb] += 1
            gate = "playbooks/workflow_gate.yml"         # in many workflows: never what tells them apart
            steps.pop(gate, None)
            ours = bool(steps) and not foreign
            entry = next((c for c in cat if c["name"] == w.get("name")), None) if ours else None
            how = "its name"
            if entry is None and ours:
                fits = [c for c in cat if steps == collections.Counter(x for x in c.get("playbooks") or [] if x != gate)]
                # one that promises an approval only on a workflow that has one
                fits = [c for c in fits if approval or "approv" not in str((c.get("description") or {}).get("supports") or "").lower()]
                if len(fits) == 1:
                    entry, how = fits[0], "the playbooks of its steps"
                elif len(fits) > 1:                  # never a guess between two of the catalog's
                    if self.wanted(w.get("name")):
                        self.note("workflow", str(w.get("name") or ""), "", "no description known", first(w.get("description")),
                                  why="its steps fit several workflows of the catalog (%s) - name it as one of them, or leave it"
                                  % ", ".join(repr(c["name"]) for c in fits[:4]))
                    continue
            if not self.wanted(w.get("name"), (entry or {}).get("name")):
                continue
            if entry is None:
                self.note("workflow", str(w.get("name") or ""), "", "no description known", first(w.get("description")),
                          why="not a workflow of the catalog (by its name and steps of project %r, or by its steps)" % p["project"]
                          if ours or not foreign else "its steps run templates of another project - left as it is")
                continue
            done.add(entry["name"])
            self.put("workflow", w, entry["name"], text_of(entry.get("description"), owner, contact))
            if how != "its name":
                self.results[-1]["why"] = ("%s; " % self.results[-1]["why"] if self.results[-1]["why"] else "") + "matched by " + how
        for c in cat:
            if c["name"] not in done and self.wanted(c["name"]):
                self.note("workflow", c["name"], c["name"], "not in AAP", why="no workflow of that name or of those steps")


def main():
    module = AnsibleModule(
        argument_spec=dict(catalog=dict(type="list", elements="dict", required=True), workflows=dict(type="list", elements="dict", default=[]),
                           organization=dict(type="str", required=True), project=dict(type="str", required=True),
                           owner=dict(type="str", default=""), contact=dict(type="str", default=""),
                           ours=dict(type="list", elements="str", default=[]), overwrite=dict(type="bool", default=False),
                           only=dict(type="list", elements="str", default=[]),
                           vendored=dict(type="list", elements="dict", default=[]),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_templates_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:                                             # never red for what it could not read: said, nothing written
        module.exit_json(changed=False, results=[], problems=[], notes=[why], twice=[])
    s = Describe(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as ex:                          # AAP not read: said (a warning); only a refused write is red
        s.notes.append(str(ex))
    module.exit_json(changed=s.wrote, results=s.results, problems=s.problems, notes=s.notes, twice=s.twice)


if __name__ == "__main__":
    main()
