#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates (1.83.0): the catalog's workflows that have nodes (aap/job_templates.yml - the STIG jobs
"STIG - full scan and upload" and "STIG - drift scan and upload") made in AAP, or checked against it, after the job
templates: every node (the checks, the gate, the approval, the update) and its success, failure and always link, the
gate's Variables, Prompt on launch for Limit, the site portal's label on the workflow (never on its steps). The same code
as AAP - set up portal actions' workflows (plugins/module_utils/site_aap_workflows.py). Never deletes. The old templates
and workflows a catalog entry replaces are named when AAP has them: "replaced by ..., delete when ready". In check mode
nothing is written; a template the job would make in the same run counts as there. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_template_workflows
short_description: Make or check the catalog's workflows in AAP (their nodes and links), and name the old ones they replace
options:
  workflows: {description: "aap/job_templates.yml's workflows - those with nodes are made.", type: list, elements: dict, required: true}
  templates: {description: "aap/job_templates.yml's job_templates (for their replaces).", type: list, elements: dict, default: []}
  organization: {description: The organization (its name)., type: str, required: true}
  uses: {description: "{a step's catalog name: the site's own template used for it} (the template job's uses: the update or the gate under another name).", type: dict, default: {}}
  renamed: {description: "Old template names the template job said to rename - not named again as old ones to delete.", type: list, elements: str, default: []}
  refused: {description: "1.83.0: steps the template job refused (another playbook or project, a Limit prompt) - a workflow with such a node is not made or changed.", type: list, elements: str, default: []}
  project: {description: "The project the steps run from (aap_templates_project). Every step of a workflow is checked here too, whatever only says.", type: str, required: true}
  planned: {description: "Check mode: the templates the job would make in this run (counted as there).", type: list, elements: str, default: []}
  fixing: {description: "Check mode: the templates the job would bring right in this run (aap_templates_overwrite) - their steps not refused for what it fixes.", type: list, elements: str, default: []}
  overwrite: {description: "Bring a workflow made by hand, or changed since, to the catalog's (its nodes made again).", type: bool, default: false}
  labels: {description: "The labels each workflow gets (the portal's label and its area's).", type: list, elements: str, default: []}
  describe: {description: "{workflow: {name: {does, targets, produces, supports}}, owner, contact} - the description written.", type: dict, default: {}}
  only: {description: "Only the workflows whose name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each: {kind (workflow|label|old), name, action (create|update|unchanged|hand-edited|not ours|skipped|label|
  old|kept|failed), diff, why}.", type: list}
problems: {description: What stopped it (the organization not found, AAP not reached)., type: list}
'''

import fnmatch
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection
from ansible.module_utils.site_aap_workflows import Sync, _q

MARK = re.compile(r"\s*\[set up by AAP - set up job templates, fingerprint ([0-9a-f]{12})\]\s*$")
MARK_TEXT = " [set up by AAP - set up job templates, fingerprint %s]"
NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it makes job templates and workflows)')


class Workflows(Sync):
    def __init__(self, api, p, check):
        Sync.__init__(self, api, p, check, MARK, MARK_TEXT, "aap_templates_overwrite")

    def wanted(self, name):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(name.casefold(), x) for x in pats)

    def label(self, oid, name):
        """The portal's labels on the workflow - added, never one taken off."""
        if not self.p["labels"]:
            return
        have = set() if oid is None or oid < 0 else set(r.get("name") for r in self.all("workflow_job_templates/%d/labels/" % oid))
        missing = [n for n in self.p["labels"] if n not in have]
        if not missing:
            return
        if self.check or oid is None or oid < 0:
            self.note("workflow", name, "label", why="would label it: %s" % ", ".join(missing))
            return
        for n in missing:
            rows = [r for r in self.all("labels/?name=%s" % _q(n)) if r.get("name") == n and r.get("organization") == self.org_id]
            lid = int(rows[0]["id"]) if rows else int(self.write("POST", "labels/", {"name": n, "organization": self.org_id})[1]["id"])
            self.write("POST", "workflow_job_templates/%d/labels/" % oid, {"id": lid})
        self.note("workflow", name, "label", why="labelled: %s" % ", ".join(missing))

    def old(self, name, replaced_by, there):
        """An old name AAP has: "delete it when ready" only when what replaces it is there (or made in this run); else kept
        (1.83.1, P2-5: never "replaced" by a workflow or template that was skipped)."""
        for api_kind, kind in (("job_templates", "job template"), ("workflow_job_templates", "workflow")):
            rows = [r for r in self.by_name(api_kind, name) if r.get("organization") == self.org_id]
            if rows and there:
                self.note("old", name, "old", why="%s replaced by '%s' - delete it when ready (nothing deletes it)" % (kind, replaced_by))
            elif rows:
                self.note("old", name, "kept", why="%s - keep it: '%s', which replaces it, is not in AAP%s (see above)"
                          % (kind, replaced_by, " and would not be made" if self.check else ""))

    def is_there(self, api_kind, name):
        """The catalog's template or workflow: in AAP (this organization), made in this run, or - Check - one it would make."""
        made = set(r["name"] for r in self.results if r.get("kind") == "workflow" and r.get("action") == "create")
        if name in made or name in (self.p["uses"] or {}):
            return True
        if api_kind == "job_templates" and self.check and name in (self.p["planned"] or []):
            return True
        return any(r.get("organization") == self.org_id for r in self.by_name(api_kind, name))

    def step_why(self, step, name):
        """1.83.0 (the director's check): the template a workflow's step resolves to, checked here for every step of every
        workflow made, whatever aap_templates_only let the template job plan - it runs the step's playbook from this site's
        project, asks for a Limit only when the catalog's step does, and asks for Variables when its node gives them. '' when
        it may be the step (or it is not in AAP: the workflow is skipped for that below; in Check, one the job would make or
        bring right)."""
        e = self.catalog.get(step)
        if not e or (self.check and name in (self.p["fixing"] or [])):
            return ""
        rows = [r for r in self.by_name("job_templates", name) if r.get("organization") == self.org_id]
        if not rows:
            return ""
        if len(rows) > 1:
            return "AAP has %d job templates named %r in the organization - rename one" % (len(rows), name)
        t = rows[0]
        if t.get("playbook") != e.get("playbook") or self.project_id is None or t.get("project") != self.project_id:
            return "%r runs %s%s - the step must run %s from this project" % (
                name, t.get("playbook") or "no playbook", "" if t.get("project") == self.project_id else " from another project",
                e.get("playbook"))
        if t.get("ask_limit_on_launch") and not e.get("ask_limit"):
            return ("%r asks for a Limit on launch: the workflow's Limit would reach it and it would do nothing - untick Prompt "
                    "on launch for Limit on it" % name)
        if e.get("ask_variables") and not t.get("ask_variables_on_launch"):
            return ("%r does not ask for Variables on launch, and its workflow node gives it Variables - tick Prompt on launch "
                    "for Variables on it" % name)
        return ""

    def run(self):
        orgs = self.by_name("organizations", self.p["organization"])
        if len(orgs) != 1:
            self.problems.append("organization %r %s in AAP" % (self.p["organization"], "is not there" if not orgs else "is there %d times" % len(orgs)))
            return
        self.org_id = int(orgs[0]["id"])
        prs = [r for r in self.by_name("projects", self.p["project"]) if r.get("organization") == self.org_id]
        self.project_id = int(prs[0]["id"]) if len(prs) == 1 else None
        self.catalog = dict((e.get("name"), e) for e in self.p["templates"] or [])
        refs = {"org": self.org_id}
        tpl_ids = dict((n, -1) for n in self.p["planned"]) if self.check else {}
        uses = self.p["uses"] or {}
        for w in self.p["workflows"]:
            if not w.get("nodes") or not self.wanted(w["name"]):
                continue
            steps = [n["template"] for n in w["nodes"] if n.get("template")]
            # 1.83.0: a step the site has under another name (its own update or gate) is that template, never a second one
            w = dict(w, nodes=[dict(n, template=uses.get(n["template"], n["template"])) if n.get("template") else n for n in w["nodes"]])
            bad = sorted(set(n["template"] for n in w["nodes"] if n.get("template") in (self.p["refused"] or [])))
            if bad:
                # 1.83.0 (Codex P1, final pass): never wired to a step that is not fit (a gate that runs the update)
                self.note("workflow", w["name"], "skipped", why="its step(s) %s refused above - not made or changed" % ", ".join(repr(x) for x in bad))
                continue
            # 1.83.0 (the director's check): every step's template checked here as well - with aap_templates_only naming the
            # workflow alone, the template job planned none of them (a "Workflow gate - has a plan" that runs the update)
            whys = []
            for step in steps:
                why = self.step_why(step, uses.get(step, step))
                if why and why not in whys:
                    whys.append(why)
            if whys:
                self.note("workflow", w["name"], "skipped", why="%s - not made or changed" % "; ".join(whys))
                continue
            try:
                oid = self.workflow(w, tpl_ids, refs)
                last = self.results[-1] if self.results else {}
                if last.get("kind") == "workflow" and last.get("action") in ("create", "update"):
                    last["nodes"] = w["nodes"]          # every node and link, in the job's output
                if oid is not None and last.get("action") not in ("skipped", "hand-edited", "not ours"):
                    self.label(oid, w["name"])
            except RuntimeError as ex:
                self.note("workflow", w["name"], "failed", why=str(ex)[:300])
                self.problems.append("stopped at workflow %r: %s" % (w["name"], ex))
                return
        for api_kind, entries in (("job_templates", self.p["templates"]), ("workflow_job_templates", self.p["workflows"])):
            for e in entries:
                olds = [n for n in e.get("replaces") or [] if n not in (self.p["renamed"] or [])]
                if olds and self.wanted(e["name"]):
                    there = self.is_there(api_kind, e["name"])
                    for n in olds:
                        self.old(n, e["name"], there)


def main():
    module = AnsibleModule(
        argument_spec=dict(workflows=dict(type="list", elements="dict", required=True), templates=dict(type="list", elements="dict", default=[]),
                           organization=dict(type="str", required=True), planned=dict(type="list", elements="str", default=[]),
                           fixing=dict(type="list", elements="str", default=[]),
                           uses=dict(type="dict", default={}), renamed=dict(type="list", elements="str", default=[]),
                           refused=dict(type="list", elements="str", default=[]), project=dict(type="str", required=True),
                           overwrite=dict(type="bool", default=False), labels=dict(type="list", elements="str", default=[]),
                           describe=dict(type="dict", default={}), only=dict(type="list", elements="str", default=[]),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_templates_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], problems=[why])
    s = Workflows(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as ex:
        s.problems.append(str(ex))
    changed = not module.check_mode and any(r["action"] in ("create", "update", "label") for r in s.results)
    module.exit_json(changed=changed, results=s.results, problems=s.problems)


if __name__ == "__main__":
    main()
