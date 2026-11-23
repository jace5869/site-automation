#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up job templates (1.83.0): the catalog's workflows that have nodes (aap/job_templates.yml - since 1.107.0 every
workflow of docs/WORKFLOWS_AND_SCHEDULES.md, but the site portal's own, made by AAP - set up portal actions, and the two
that "STIG - drift scan and upload" replaces) made in AAP, or checked against it, after the job templates: every node
(the checks, the gates, the approvals, the updates) and its success, failure and always link, each node's Variables,
Job type and Show changes, Prompt on launch for Limit and Variables, the workflow's Variables, its survey (written when
it makes the workflow - with overwrite when AAP has none - never changed after), the site portal's label on a workflow the catalog marks portal (never on its
steps). A workflow of the site's own whose steps are the catalog workflow's, under another name, is never made twice. The same code
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
  create_missing: {description: "1.107.0: false = a workflow AAP has not is not made, only said (aap_templates_create_missing).", type: bool, default: true}
  create_alias: {description: "1.107.0: true = made even when a workflow of the site's has its steps under another name (aap_templates_create_alias).", type: bool, default: false}
  templates: {description: "aap/job_templates.yml's job_templates (for their replaces).", type: list, elements: dict, default: []}
  organization: {description: The organization (its name)., type: str, required: true}
  uses: {description: "{a step's catalog name: the site's own template used for it} (the template job's uses: the update or the gate under another name).", type: dict, default: {}}
  renamed: {description: "Old template names the template job said to rename - not named again as old ones to delete.", type: list, elements: str, default: []}
  refused: {description: "1.83.0: steps the template job refused (another playbook or project, a Limit prompt) - a workflow with such a node is not made or changed.", type: list, elements: str, default: []}
  project: {description: "The project the steps run from (aap_templates_project). Every step of a workflow is checked here too, whatever only says.", type: str, required: true}
  planned: {description: "Check mode: the templates the job would make in this run (counted as there).", type: list, elements: str, default: []}
  fixing: {description: "Check mode: the templates the job would bring right in this run - {name: [what it fixes, as it says them]} (1.107.0), or a list of names (every field) - checked as they will be.", type: raw, default: {}}
  survey_adds: {description: "1.107.0: {template name: [the survey questions the job adds in this run]} - counted as there.", type: dict, default: {}}
  renamed_workflows: {description: "1.107.0: Check mode - {old name: new name} of the workflows the renames of this run rename.", type: dict, default: {}}
  overwrite: {description: "Bring a workflow made by hand, or changed since, to the catalog's (its nodes made again).", type: bool, default: false}
  labels: {description: "The labels a workflow the catalog marks portal gets (the portal's label and its area's) - 1.107.0: the others none.", type: list, elements: str, default: []}
  alone_label: {description: "1.92.0: the label that keeps a step template on the portal as a card of its own (portal_label_alone).", type: str, default: portal-alone}
  describe: {description: "{workflow: {name: {does, targets, produces, supports}}, owner, contact} - the description written.", type: dict, default: {}}
  known: {description: Fingerprints keyed by organization ID/kind/name from the latest setup Run with a map., type: dict}
  only: {description: "Only the workflows whose name matches one of these patterns (* and ?, any case); [] = all.", type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each: {kind (workflow|label|old|step label), name, action (create|update|unchanged|hand-edited|not ours|skipped|label|attention|
  old|kept|failed|left|other name|missing), diff, diff_lines (1.107.0: field by field, node by node), survey, why}.", type: list}
problems: {description: What stopped it (the organization not found, AAP not reached)., type: list}
'''

import fnmatch
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection
from ansible.module_utils.site_aap import WORKFLOW_GATE, vars_have, vars_of, workflow_steps
from ansible.module_utils.site_aap_workflows import Sync, _q, diff_lines, limit_misses_localhost

MARK = re.compile(r" \[set up by AAP - set up job templates, fingerprint ([0-9a-f]{12})\]\Z")
NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it makes job templates and workflows)')


def answer_why(q, v):
    """1.107.0 (review): what keeps an answer from fitting an AAP survey question - '' when it fits (its type, its choices,
    its bounds)."""
    t = str(q.get("type") or "text")
    ch = q.get("choices")
    allowed = [str(c).strip() for c in (ch.splitlines() if isinstance(ch, str) else ch or []) if str(c).strip()]
    if t == "multiplechoice":
        if isinstance(v, (list, tuple, dict)):
            return "takes one choice"
        return "" if str(v) in allowed else "does not offer %s" % v
    if t == "multiselect":
        if not isinstance(v, (list, tuple)):
            return "takes a list"
        bad = [str(x) for x in v if str(x) not in allowed]
        return ("does not offer %s" % ", ".join(bad)) if bad else ""
    if t in ("integer", "float"):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (t == "integer" and not isinstance(v, int)):
            return "takes a number"
        if q.get("min") not in (None, "") and v < float(q["min"]) or q.get("max") not in (None, "") and v > float(q["max"]):
            return "takes %s to %s" % (q.get("min"), q.get("max"))
        return ""
    if not isinstance(v, str):
        return "takes text"
    if q.get("min") not in (None, "") and len(v) < int(q["min"]) or q.get("max") not in (None, "") and len(v) > int(q["max"]):
        return "takes %s to %s characters" % (q.get("min"), q.get("max"))
    return ""


class Workflows(Sync):
    def __init__(self, api, p, check):
        Sync.__init__(self, api, p, check, MARK, "aap_templates_overwrite")

    def wanted(self, name):
        pats = [str(x).casefold() for x in self.p["only"] or []]
        return not pats or any(fnmatch.fnmatchcase(name.casefold(), x) for x in pats)

    def label(self, oid, name, portal=True):
        """The portal's labels on the workflow - added, never one taken off. 1.107.0: only on a workflow the catalog marks
        portal (the STIG jobs); the others are published with AAP - set up portal actions (portal_publish) when wanted."""
        if not self.p["labels"] or not portal:
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

    def survey_qs(self, t):
        """{variable: question} of a job template's survey when it is on - {} when it has none or it is off."""
        if not t.get("survey_enabled"):
            return {}
        code, d = self.api.call("GET", "job_templates/%d/survey_spec/" % int(t["id"]))
        if code != 200:
            raise RuntimeError("the survey of job template %r could not be read (HTTP %d)" % (t.get("name"), code))
        return dict((str(q.get("variable")), q) for q in (d or {}).get("spec") or [])

    def survey_vars(self, t):
        """The variables of a job template's survey when it is on - [] when it has none."""
        return list(self.survey_qs(t))

    def node_why(self, t, e, needs, made_here):
        """1.107.0: what a step's nodes give it that its template does not take - Variables (a survey question, or Prompt
        on launch for Variables), Job type, Show changes. t: the template as AAP has it (with what this run brings right
        set as the catalog's); None: made in this run, as the catalog's entry e has it. The survey questions the job adds
        in this run (survey_adds) count as there. '' when it takes them."""
        name = (t or {}).get("name") or e.get("name")
        asks_vars = bool(t.get("ask_variables_on_launch")) if t else bool(e.get("ask_variables"))
        keys = sorted(needs.get("vars") or [])
        if not keys:
            qs, adds = {}, []
        else:
            qs = self.survey_qs(t) if t else {}
            adds = list((self.p.get("survey_adds") or {}).get(name) or [])
        if keys and not asks_vars:
            have = list(qs) + adds
            miss = [k for k in keys if k not in have]
            if miss:
                off = t and not t.get("survey_enabled") and not adds
                return ("%r %s no survey question %s and does not ask for Variables on launch - its workflow node gives %s: "
                        "add the question%s (docs/SETUP_AAP.md, the template's survey)%s, then run this again" % (
                            name, "would be made with" if made_here else "has", ", ".join(miss), "them" if len(miss) > 1 else "it",
                            "s" if len(miss) > 1 else "", " - or turn its survey on (it is off)" if off else ""))
        # 1.107.0 (review): every answer a node gives that a question of the template's survey (on) asks must fit that
        # question - its type and its choices - whatever Prompt on launch for Variables says: AAP refuses the node else
        for k in keys:
            q = qs.get(k)
            if not q:
                continue
            for v in (needs.get("answers") or {}).get(k) or []:
                why = answer_why(q, v)
                if why:
                    return ("%r's survey question %s %s, and its workflow node answers %s - change the question (never changed "
                            "here), then run this again" % (name, k, why, json.dumps(v)))
        for flag, field, word in (("job_type", "ask_job_type_on_launch", "Job type"), ("diff_mode", "ask_diff_mode_on_launch", "Show changes")):
            if needs.get(flag):
                asks = bool(t.get(field)) if t else bool(e.get("ask_" + flag))
                if not asks:
                    return ("%r does not ask for %s on launch, and its workflow node sets it - tick Prompt on launch for %s on "
                            "it%s" % (name, word, word, " (aap_templates_overwrite: true brings it to the catalog's)" if t else ""))
        return ""

    def fixed(self, t, e):
        """1.107.0 (review): Check - the template as it will be after this run: the fields the template job brings right
        (fixing: {catalog name: [what it fixes, as it says them]}) set as the catalog's, the others as AAP has them."""
        fixing = self.p.get("fixing") or {}
        if isinstance(fixing, list):                   # every field (the older form)
            fixing = dict((n, None) for n in fixing)
        if not self.check or e.get("name") not in fixing:
            return t
        what = fixing[e["name"]]
        everything = what is None
        what = list(what or [])
        t = dict(t)
        if everything or "playbook" in what:
            t["playbook"] = e.get("playbook")
        if everything or "project" in what:
            t["project"] = self.project_id
        for word, field, key in (("prompt for Limit", "ask_limit_on_launch", "ask_limit"),
                                 ("prompt for Variables", "ask_variables_on_launch", "ask_variables"),
                                 ("prompt for Job Type", "ask_job_type_on_launch", "ask_job_type"),
                                 ("prompt for Show changes", "ask_diff_mode_on_launch", "ask_diff_mode")):
            if everything or word in what:
                t[field] = bool(e.get(key))
        if everything or "Job type" in what:
            t["job_type"] = e.get("job_type") or "run"
        if e.get("diff_mode") and (everything or "Show changes" in what):
            t["diff_mode"] = True
        fixed_vars = dict((k, v) for k, v in (e.get("extra_vars") or {}).items() if everything or ("variable %s" % k) in what)
        if fixed_vars:
            t["extra_vars"] = json.dumps(dict(vars_of(t.get("extra_vars")), **fixed_vars))
        return t

    def step_why(self, step, name, needs=None):
        """1.83.0 (the director's check): the template a workflow's step resolves to, checked here for every step of every
        workflow made, whatever aap_templates_only let the template job plan - it runs the step's playbook from this site's
        project, asks for a Limit only when the catalog's step does, asks for Variables when the catalog's does, has the
        catalog's fixed Variables (1.107.0: the plan var, the workflow mode), and takes what its nodes give. '' when it may
        be the step (or it is not in AAP: the workflow is skipped for that below; in Check, one the job would make)."""
        e = self.catalog.get(step)
        if not e:
            return ""
        rows = [r for r in self.by_name("job_templates", name) if r.get("organization") == self.org_id]
        if not rows:
            # Check: one the job would make in this run - it gets the catalog's prompts and Variables (a Run reads the one
            # it made; one not in AAP skips the workflow below)
            made_here = self.check and name in (self.p["planned"] or [])
            return self.node_why(None, e, needs or {}, True) if made_here and needs else ""
        if len(rows) > 1:
            return "AAP has %d job templates named %r in the organization - rename one" % (len(rows), name)
        t = self.fixed(rows[0], e)
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
        # 1.107.0 (review): a controller-side step whose own Limit leaves out localhost runs nothing and ends green
        if not e.get("ask_limit") and limit_misses_localhost(t.get("limit")):
            return ("%r has its own Limit %r, which leaves out localhost: it would run nothing and end green - empty its Limit"
                    % (name, t.get("limit")))
        # 1.107.0 (review): a dry run before an approval is a Check template itself; every other step (the one after the
        # approval too) a Run template - no node sets a Job type, so the template's own is what runs
        if str(t.get("job_type") or "run") != (e.get("job_type") or "run"):
            return ("%r is a %s template: %s (aap_templates_overwrite: true sets it)" % (
                name, str(t.get("job_type") or "run").capitalize(),
                "the dry run before the approval must be Job type Check on the template itself" if e.get("job_type") == "check"
                else "the step must be Job type Run, or it would apply nothing and end green"))
        if e.get("diff_mode") and not t.get("diff_mode"):
            return "%r does not have Show changes on: its dry run must show what would change (aap_templates_overwrite: true sets it)" % name
        # 1.107.0 (review): the fixed Variables the step's node relies on (a check in report mode never hands on its plan)
        if e.get("extra_vars") and not vars_have(t.get("extra_vars"), e["extra_vars"]):
            have = vars_of(t.get("extra_vars"))
            wrong = ["%s: %s" % (k, v) for k, v in sorted(e["extra_vars"].items()) if str(have.get(k)).lower() != str(v).lower()]
            return ("%r lacks its fixed Variables %s - the workflow relies on them (aap_templates_overwrite: true sets them, or put "
                    "them in its Variables)" % (name, ", ".join(wrong)))
        return self.node_why(t, e, needs or {}, False) if needs else ""

    # ---- 1.107.0: a workflow of the site's own with the catalog workflow's steps, under another name - never made twice
    def renamed_wf(self):
        """Check: {old name: new name} of the workflows aap_templates_rename would rename in this run."""
        return (self.p.get("renamed_workflows") or {}) if self.check else {}

    def by_name(self, kind, name, extra=""):
        """1.107.0 (review): Check - a workflow the renames of this run would give the catalog's name is read under it."""
        rows = Sync.by_name(self, kind, name, extra)
        ren = self.renamed_wf()
        if kind != "workflow_job_templates" or not ren:
            return rows
        rows = [r for r in rows if ren.get(r.get("name"), r.get("name")) == name]
        for old, new in sorted(ren.items()):
            if new == name and old != name:
                rows = rows + [dict(r, name=new) for r in Sync.by_name(self, kind, old, extra)]
        return rows

    def same_steps(self, w):
        if not hasattr(self, "_site_wfs"):
            names = set(x["name"] for x in self.p["workflows"])
            ren = self.renamed_wf()
            self._site_wfs = [r for r in self.all("workflow_job_templates/?organization=%d" % self.org_id)
                              if r.get("organization") == self.org_id and ren.get(r.get("name"), r.get("name")) not in names]
            self._pb_of = dict((int(t["id"]), str(t.get("playbook") or "")) for t in self.all("job_templates/?organization=%d" % self.org_id)
                               if t.get("organization") == self.org_id and (self.project_id is None or t.get("project") == self.project_id))
            self._steps = {}
        import collections
        want = collections.Counter(x for x in w.get("playbooks") or [] if x != WORKFLOW_GATE)
        approval = any(n.get("approval") for n in w.get("nodes") or [])
        out = []
        for r in self._site_wfs:
            rid = int(r["id"])
            if rid not in self._steps:
                try:
                    nodes = self.all("workflow_job_template_nodes/?workflow_job_template=%d" % rid)
                except RuntimeError:
                    nodes = []                 # 1.107.0 (review): gone since the list was read - not a workflow that fits

                other = [n for n in nodes if ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get(
                    "unified_job_type") not in ("job", "workflow_approval")]
                steps, foreign, appr = workflow_steps(nodes, self._pb_of)
                self._steps[rid] = (steps, appr) if steps and not foreign and not other else (None, False)
            steps, appr = self._steps[rid]
            # 1.107.0 (review): the catalog workflow's own Variables tell two of the same steps apart (Mail relay add / remove)
            if steps == want and appr == approval and (not w.get("extra_vars") or vars_have(r.get("extra_vars"), w["extra_vars"])):
                out.append(str(r.get("name")))
        return out

    def one(self, w, tpl_ids, refs, uses):
        """One catalog workflow: left, skipped (said why), or made / checked / brought right."""
        if not w.get("nodes"):
            if w.get("deferred"):
                self.note("workflow", w["name"], "left", why=w["deferred"])
                if w.get("survey"):
                    rows = [r for r in self.by_name("workflow_job_templates", w["name"]) if r.get("organization") == self.org_id]
                    if len(rows) == 1:
                        self.survey(int(rows[0]["id"]), w, add_missing=True)
            elif w.get("made_by"):             # 1.107.0: the site portal's own workflows - its setup job makes them
                self.note("workflow", w["name"], "left", why="%s makes it (its own nodes and templates): run that job" % w["made_by"])
            return
        steps = [n["template"] for n in w["nodes"] if n.get("template")]
        needs = {}                             # 1.107.0: what each step's nodes give its template (Variables, Job type, Show changes)
        for n in w["nodes"]:
            if n.get("template"):
                d = needs.setdefault(n["template"], {"vars": set()})
                d["vars"].update((n.get("extra_data") or {}).keys())
                for k, v in (n.get("extra_data") or {}).items():
                    d.setdefault("answers", {}).setdefault(k, []).append(v)
                d["job_type"] = d.get("job_type") or bool(n.get("job_type"))
                d["diff_mode"] = d.get("diff_mode") or bool(n.get("diff_mode"))
        if not [r for r in self.by_name("workflow_job_templates", w["name"]) if r.get("organization") == self.org_id]:
            if not self.p.get("create_missing", True):
                self.note("workflow", w["name"], "missing", why="not made (aap_templates_create_missing: false)")
                return
            other = [] if self.p.get("create_alias") else self.same_steps(w)
            if other:
                self.note("workflow", w["name"], "other name", why="%s has its steps - not made twice; rename it to the catalog's "
                          "name (aap_templates_rename_map), or aap_templates_create_alias: true makes this one beside it"
                          % ", ".join(repr(x) for x in other[:3]))
                return
        # 1.107.0 (review): a survey on the workflow there whose question would override what its steps fix (a node's answers,
        # a step's fixed Variables: the phase, the gate's plan var, the workflow mode) - never wired; the survey never edited
        rows = [r for r in self.by_name("workflow_job_templates", w["name"]) if r.get("organization") == self.org_id]
        if len(rows) == 1:
            code, spec = self.api.call("GET", "workflow_job_templates/%d/survey_spec/" % int(rows[0]["id"]))
            if code != 200:
                raise RuntimeError("its survey could not be read (HTTP %d)" % code)
            protected = set((w.get("extra_vars") or {}).keys())      # the workflow's own too (mail_relay_action)
            for n in w["nodes"]:
                protected.update((n.get("extra_data") or {}).keys())
                protected.update(((self.catalog.get(n.get("template")) or {}).get("extra_vars") or {}).keys())
            hit = sorted(set(str(q.get("variable")) for q in (spec or {}).get("spec") or []) & protected)
            if hit:
                self.note("workflow", w["name"], "skipped", why="its survey asks %s, and a workflow's answer would override what its "
                          "steps fix - take the question off its survey, then run this again (never changed here)" % ", ".join(hit))
                return
        # 1.83.0: a step the site has under another name (its own update or gate) is that template, never a second one
        w = dict(w, nodes=[dict(n, template=uses.get(n["template"], n["template"])) if n.get("template") else n for n in w["nodes"]])
        bad = sorted(set(n["template"] for n in w["nodes"] if n.get("template") in (self.p["refused"] or [])))
        if bad:
            # 1.83.0 (Codex P1, final pass): never wired to a step that is not fit (a gate that runs the update)
            self.note("workflow", w["name"], "skipped", why="its step(s) %s refused above - not made or changed" % ", ".join(repr(x) for x in bad))
            return
        # 1.83.0 (the director's check): every step's template checked here as well - with aap_templates_only naming the
        # workflow alone, the template job planned none of them (a "Workflow gate - has a plan" that runs the update)
        whys = []
        for step in steps:
            why = self.step_why(step, uses.get(step, step), needs.get(step))
            if why and why not in whys:
                whys.append(why)
        if whys:
            self.note("workflow", w["name"], "skipped", why="%s - not made or changed" % "; ".join(whys))
            return
        oid = self.workflow(w, tpl_ids, refs)
        last = next((r for r in reversed(self.results) if r.get("kind") == "workflow" and r.get("name") == w["name"]), {})
        if last.get("action") in ("create", "update"):
            last["nodes"] = w["nodes"]                  # every node and link, in the job's output
        if last.get("action") in ("update", "hand-edited", "not ours") and last.get("diff"):
            last["diff_lines"] = diff_lines(last["diff"])     # 1.107.0: field by field, node by node
        if oid is not None and last.get("action") not in ("skipped", "hand-edited", "not ours"):
            self.label(oid, w["name"], bool(w.get("portal")))

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
            if not self.wanted(w["name"]):
                continue
            # 1.107.0 (review): every read and write of one workflow here - an error is said for it, and the next ones go on
            try:
                self.one(w, tpl_ids, refs, uses)
            except RuntimeError as ex:
                self.note("workflow", w["name"], "failed", why=str(ex)[:300])
                self.problems.append("workflow %r: %s" % (w["name"], ex))
        for api_kind, entries in (("job_templates", self.p["templates"]), ("workflow_job_templates", self.p["workflows"])):
            for e in entries:
                olds = [n for n in e.get("replaces") or [] if n not in (self.p["renamed"] or [])]
                if olds and self.wanted(e["name"]):
                    there = self.is_there(api_kind, e["name"])
                    for n in olds:
                        self.old(n, e["name"], there)
        # 1.92.0: a step template that carries the portal's label is said (never removed): the portal shows the workflow
        if self.p["labels"]:
            self.step_labels(self.p["labels"][0], self.p.get("alone_label") or "portal-alone")


def main():
    module = AnsibleModule(
        argument_spec=dict(workflows=dict(type="list", elements="dict", required=True), templates=dict(type="list", elements="dict", default=[]),
                           organization=dict(type="str", required=True), planned=dict(type="list", elements="str", default=[]),
                           fixing=dict(type="raw", default={}), survey_adds=dict(type="dict", default={}),
                           renamed_workflows=dict(type="dict", default={}),
                           uses=dict(type="dict", default={}), renamed=dict(type="list", elements="str", default=[]),
                           refused=dict(type="list", elements="str", default=[]), project=dict(type="str", required=True),
                           overwrite=dict(type="bool", default=False), labels=dict(type="list", elements="str", default=[]),
                           alone_label=dict(type="str", default="portal-alone"),
                           create_missing=dict(type="bool", default=True), create_alias=dict(type="bool", default=False),
                           describe=dict(type="dict", default={}), known=dict(type="dict", default=None), only=dict(type="list", elements="str", default=[]),
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
    changed = not module.check_mode and (s.wrote_survey or any(r["action"] in ("create", "update", "label") for r in s.results))
    module.exit_json(changed=changed, results=s.results, problems=s.problems, fingerprints=s.out, org_id=getattr(s, "org_id", None))


if __name__ == "__main__":
    main()
