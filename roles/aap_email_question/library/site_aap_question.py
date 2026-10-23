#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Add one survey question - "Also email the report to" (report_email_also) - to the AAP job templates that run
the report playbooks, and to the workflows that run those templates, through AAP's own API
(playbooks/aap_email_question.yml). The address and login come from the "Red Hat Ansible Automation Platform"
credential's variables (plugins/module_utils/site_aap.py). Standard library only. The templates are found by
their playbook, so your own template names work. A question already there is left as it is; the rest of the
survey is never changed; a survey that is turned off is not turned on."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_question
short_description: Add a survey question to the AAP templates that run some playbooks (or take it out again)
description:
  - Finds every job template whose playbook is one of O(playbooks) (the file name; O(project) narrows it to one
    project), the job templates and workflows named in O(templates), and - O(workflows) - every workflow job
    template that runs one of them (a workflow inside a workflow too). O(exclude) leaves some out.
  - "state=present: adds O(question) at the end of each one's survey. A template without a survey gets one, with
    this question alone, turned on. A survey that has the question (its variable) is left as it is. A survey that
    is turned off gets the question but stays off: turning it on would ask its other questions too, and their
    defaults would reach every run - the job says so."
  - "state=absent: takes the question out again. A survey left with no question is removed and turned off."
  - A job inside a workflow is never asked its own survey (AAP asks the workflow's), so the workflows need the
    question themselves.
  - In check mode it only reads, and says what it would change.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential
    sets it (CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and CONTROLLER_PASSWORD,
    CONTROLLER_VERIFY_SSL, or the AAP_* / TOWER_* names). Its user needs the Admin role on each template.
options:
  question:
    description: "The question: variable, question_name, question_description, max (characters, default 4096).
      A text box of several lines (a list pasted keeps its line breaks), not required, empty by default."
    type: dict
    required: true
  playbooks: {description: "Job templates whose playbook is one of these (playbooks/cert_report.yml or cert_report.yml).", type: list, elements: str, default: []}
  templates: {description: "More job templates or workflows, by name or ID.", type: list, elements: str, default: []}
  exclude: {description: "Templates left out, by name or ID.", type: list, elements: str, default: []}
  workflows: {description: "Also every workflow job template that runs one of the job templates found.", type: bool, default: true}
  project: {description: "Only job templates of this AAP project (its name); empty = any project.", type: str, default: ''}
  state: {description: present adds the question, absent takes it out., type: str, choices: [present, absent], default: present}
  warn_variables: {description: "A survey question with one of these variables is pointed out (report_email_to replaces the usual recipients).", type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
api: {description: The API path that answered., type: str, sample: api/controller/v2/}
problems: {description: AAP could not be reached, refused the login, or a template named in templates is not there., type: list}
results:
  description: "Each template: what was done - added, already there, removed, not there, failed (error) - and warnings."
  type: list
  sample: [{template: Certificate report, kind: job, id: 12, playbook: playbooks/cert_report.yml, action: added, changed: true,
            ok: true, error: "", warnings: []}]
'''

import copy
import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the report '
                 'templates and their workflows)')
KINDS = {"job": ("job_templates", "job template"), "workflow": ("workflow_job_templates", "workflow")}


def make_question(q):
    """The survey question AAP keeps: a text box of several lines (textarea: a one-line box drops the line breaks of a
    pasted list), not required, empty by default."""
    q = q or {}
    return {"question_name": str(q.get("question_name") or q.get("variable")), "question_description": str(q.get("question_description") or ""),
            "required": False, "type": "textarea", "variable": str(q.get("variable")), "min": 0, "max": int(q.get("max") or 4096),
            "default": "", "choices": ""}


def add_question(spec, question, enabled=True, warn_variables=()):
    """(the new survey or None when nothing changes, what is done, [warnings]). Pure: no AAP, so it can be tested on its own.
    what: 'added', 'created' (there was no survey: this question alone; turn the survey on), 'there' (left as it is)."""
    spec = copy.deepcopy(spec or {})
    qs = [x for x in (spec.get("spec") or []) if isinstance(x, dict)]
    var = question["variable"]
    warnings = ["its survey asks %s too: what is typed there replaces the template's usual recipients (Also email the report to adds "
                "to them) - take that question out if it was meant the same way" % x.get("variable")
                for x in qs if x.get("variable") in warn_variables and x.get("variable") != var]
    if any(x.get("variable") == var for x in qs):
        if not enabled:
            warnings.append("its survey is turned off: the question is there, but not asked until you turn the survey on")
        return None, "there", warnings
    if not qs:
        return {"name": str(spec.get("name") or ""), "description": str(spec.get("description") or ""), "spec": [question]}, "created", warnings
    if not enabled:
        warnings.append("its survey is turned off, and was left off (turning it on asks its other questions too, and their defaults "
                        "reach every run): the question is added, but asked only once you turn the survey on")
    spec["name"], spec["description"] = str(spec.get("name") or ""), str(spec.get("description") or "")
    spec["spec"] = qs + [question]
    return spec, "added", warnings


def remove_question(spec, variable):
    """(the new survey - {} when no question is left - or None when nothing changes, what is done: 'removed', 'emptied', 'absent')."""
    spec = copy.deepcopy(spec or {})
    qs = [x for x in (spec.get("spec") or []) if isinstance(x, dict)]
    keep = [x for x in qs if x.get("variable") != variable]
    if len(keep) == len(qs):
        return None, "absent"
    if not keep:
        return {}, "emptied"
    spec["spec"] = keep
    return spec, "removed"


def _base(path):
    return os.path.basename(str(path or "").strip().replace("\\", "/"))


def pick_jobs(jobs, playbooks, project=""):
    """The job templates whose playbook (its file name) is one of playbooks, in this project when one is given."""
    want = set(_base(p) for p in playbooks or [] if str(p).strip())
    out = []
    for j in jobs or []:
        pj = (((j.get("summary_fields") or {}).get("project") or {}).get("name")) or ""
        if _base(j.get("playbook")) in want and (not project or pj == project):
            out.append(j)
    return out


def pick_workflows(nodes_by_wf, job_ids, workflow_ids=()):
    """The workflows that run one of these job templates or workflows, or a workflow that does (any depth) - and
    workflow_ids themselves. nodes_by_wf: {workflow id: [(unified job template id, its type: job, workflow_job ...)]}."""
    job_ids, found = set(job_ids), set(workflow_ids)
    while True:
        more = set(w for w, nodes in nodes_by_wf.items() if w not in found and any(
            (t == "job" and i in job_ids) or (t == "workflow_job" and i in found) for i, t in nodes))
        if not more:
            return found
        found |= more


def _all(api, path):
    """Every page of a list: (records, why not)."""
    out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
    for _ in range(500):
        code, d = api.call("GET", nxt)
        if code != 200:
            return out, "%s could not be read (HTTP %d): %s" % (path.split("?")[0], code, error_text(d))
        out += d.get("results") or []
        n = d.get("next")
        if not n:
            return out, ""
        nxt = n.split(api.base, 1)[1] if api.base in n else n.lstrip("/")
    return out, ""


def one(api, rec, kind, p, check_mode):
    r = {"template": rec.get("name") or "", "kind": kind, "id": rec.get("id"), "playbook": rec.get("playbook") or "",
         "action": "", "changed": False, "ok": False, "error": "", "warnings": []}
    if ((rec.get("summary_fields") or {}).get("user_capabilities") or {}).get("edit") is False:
        r["error"] = "the AAP user may not change this %s: give it the Admin role on it" % KINDS[kind][1]
        return r
    path = "%s/%s/" % (KINDS[kind][0], r["id"])
    code, spec = api.call("GET", path + "survey_spec/")
    if code != 200:
        r["error"] = "its survey could not be read (HTTP %d): %s" % (code, error_text(spec))
        return r
    enabled = bool(rec.get("survey_enabled"))
    q = make_question(p["question"])
    writes = []
    if p["state"] == "present":
        new, what, r["warnings"] = add_question(spec, q, enabled, p["warn_variables"])
        r["action"] = {"there": "already there", "added": "added", "created": "added (a new survey, turned on)"}[what]
        if new is not None:
            writes.append(("POST", path + "survey_spec/", new, "the new survey"))
        if what == "created" and not enabled:
            writes.append(("PATCH", path, {"survey_enabled": True}, "turning the survey on"))
    else:
        new, what = remove_question(spec, q["variable"])
        r["action"] = {"absent": "not there", "removed": "removed", "emptied": "removed (no question left: survey removed, turned off)"}[what]
        if what == "removed":
            writes.append(("POST", path + "survey_spec/", new, "the survey without the question"))
        elif what == "emptied":
            writes.append(("DELETE", path + "survey_spec/", None, "removing the survey"))
            if enabled:
                writes.append(("PATCH", path, {"survey_enabled": False}, "turning the survey off"))
    if writes and not check_mode:
        for method, where, body, what_write in writes:
            code, d = api.call(method, where, body)
            if code not in (200, 201, 202, 204):
                r["error"] = "AAP refused %s (HTTP %d): %s" % (what_write, code, error_text(d))
                return r
    r["changed"] = bool(writes)
    r["ok"] = True
    return r


def run(api, p, check_mode):
    """(results, problems)."""
    jobs, why = _all(api, KINDS["job"][0] + "/")
    if why:
        return [], ["the job templates: " + why]
    wfs, why = _all(api, KINDS["workflow"][0] + "/") if p["workflows"] or p["templates"] else ([], "")
    if why:
        return [], ["the workflows: " + why]
    problems, picked = [], {}
    for j in pick_jobs(jobs, p["playbooks"], p["project"]):
        picked[("job", j["id"])] = j
    for name in p["templates"]:
        name = str(name).strip()
        hits = [("job", x) for x in jobs if name in (str(x.get("id")), x.get("name"))] + [("workflow", x) for x in wfs if name in (str(x.get("id")), x.get("name"))]
        if not hits:
            problems.append("templates: no job template or workflow %s that the AAP user can see" % name)
        elif len(hits) > 1:
            problems.append("templates: %d templates are named %s (%s): name it by its ID" % (len(hits), name, ", ".join(
                "%s %s" % (KINDS[k][1], x.get("id")) for k, x in hits)))
        else:
            picked[(hits[0][0], hits[0][1]["id"])] = hits[0][1]
    if p["workflows"]:
        nodes = {}
        for w in wfs:
            ns, why = _all(api, "%s/%s/workflow_nodes/" % (KINDS["workflow"][0], w["id"]))
            if why:
                problems.append("the steps of workflow %s: %s" % (w.get("name"), why))
            nodes[w["id"]] = [(n.get("unified_job_template"), ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get("unified_job_type"))
                              for n in ns]
        # a workflow named in templates counts too: the workflows that run it get the question
        found = pick_workflows(nodes, [i for k, i in picked if k == "job"], [i for k, i in picked if k == "workflow"])
        for w in wfs:
            if w["id"] in found:
                picked[("workflow", w["id"])] = w
    skip = set(str(x).strip() for x in p["exclude"] if str(x).strip())
    order = sorted(picked.items(), key=lambda kv: (kv[0][0] != "job", str(kv[1].get("name")).lower(), kv[0][1]))
    results = []
    for (kind, _), rec in order:
        if str(rec.get("id")) in skip or rec.get("name") in skip:
            continue
        try:
            results.append(one(api, rec, kind, p, check_mode))
        except Exception as e:                  # noqa: BLE001 - one template's surprise never stops the others
            results.append({"template": rec.get("name") or "", "kind": kind, "id": rec.get("id"), "playbook": rec.get("playbook") or "",
                            "action": "", "changed": False, "ok": False, "error": "unexpected answer from AAP: %s" % e, "warnings": []})
    return results, problems


def main():
    module = AnsibleModule(argument_spec=dict(
        question=dict(type="dict", required=True), playbooks=dict(type="list", elements="str", default=[]),
        templates=dict(type="list", elements="str", default=[]), exclude=dict(type="list", elements="str", default=[]),
        workflows=dict(type="bool", default=True), project=dict(type="str", default=""),
        state=dict(type="str", choices=["present", "absent"], default="present"),
        warn_variables=dict(type="list", elements="str", default=[]),
        ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)), supports_check_mode=True)
    p = module.params
    if not str((p["question"] or {}).get("variable") or "").strip():
        module.fail_json(msg="question needs a variable")
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_email_question_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, api=api.base, problems=[why], results=[])
    results, problems = run(api, p, module.check_mode)
    module.exit_json(changed=any(r["changed"] for r in results), api=api.base, problems=problems, results=results)


if __name__ == "__main__":
    main()
