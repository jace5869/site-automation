#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write the survey questions of a job into the AAP job templates that run its playbook - the outage AAR and SOP jobs
(playbooks/incident_docs_survey.yml) - through AAP's own API, so nobody types twenty questions by hand. The address and
login come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py).
Standard library only. A question already there (its variable) is left as it is - your own wording, choices or
defaults stay; the survey's other questions are never changed."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_survey_add
short_description: Add a job's survey questions to the AAP job templates that run its playbook (or take them out)
description:
  - For each entry of O(surveys) - a playbook and its questions - finds every job template whose playbook is that file
    (O(project) narrows it to one project) and adds the questions that are not there yet, in order, after the ones
    that are. A template without a survey gets one. The survey is turned on - these jobs read their input from it.
  - state=absent takes the questions out again; a survey left with none is removed and turned off.
  - In check mode it only reads, and says what it would change.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential sets it.
    Its user needs the Admin role on each template.
options:
  surveys:
    description: "[{playbook: incident_aar.yml, questions: [AAP survey questions: variable, question_name, type ...]}]"
    type: list
    elements: dict
    required: true
  project: {description: "Only job templates of this AAP project (its name); empty = any project.", type: str, default: ''}
  state: {description: present adds the questions, absent takes them out., type: str, choices: [present, absent], default: present}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
problems: {description: AAP could not be reached or read., type: list}
results:
  description: Each template - what was done (added, already there, removed, not there), the questions added or removed.
  type: list
  sample: [{template: Outage AAR, id: 42, playbook: playbooks/incident_aar.yml, action: "added 22 question(s), survey turned on",
            added: [incident_name], changed: true, ok: true, error: ""}]
missing: {description: The playbooks no job template runs (make the template first)., type: list}
'''

import copy
import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the '
                 'outage AAR and SOP templates)')
KEYS = ("variable", "question_name", "question_description", "type", "required", "default", "choices", "min", "max")


def _base(path):
    return os.path.basename(str(path or "").strip().replace("\\", "/"))


def clean_question(q):
    """A question as AAP keeps it: only its known keys; choices for a choice question only."""
    out = dict((k, copy.deepcopy(q[k])) for k in KEYS if k in q)
    out.setdefault("question_name", out.get("variable"))
    out.setdefault("question_description", "")
    out.setdefault("required", False)
    out.setdefault("default", "")
    if out.get("type") not in ("multiplechoice", "multiselect"):
        out["choices"] = ""                     # as AAP keeps a text question
    return out


def add_questions(spec, questions):
    """(the new survey or None when nothing changes, [the variables added]). Pure: no AAP."""
    spec = copy.deepcopy(spec or {})
    have = set(str(q.get("variable")) for q in spec.get("spec") or [])
    added = [clean_question(q) for q in questions if str(q.get("variable")) not in have]
    if not added:
        return None, []
    spec["spec"] = list(spec.get("spec") or []) + added
    spec.setdefault("name", "")
    spec.setdefault("description", "")
    return spec, [q["variable"] for q in added]


def remove_questions(spec, variables):
    """(the new survey - {} when none is left - or None when nothing changes, [the variables removed])."""
    want = set(variables)
    old = list((spec or {}).get("spec") or [])
    keep = [q for q in old if str(q.get("variable")) not in want]
    gone = [q.get("variable") for q in old if str(q.get("variable")) in want]
    if not gone:
        return None, []
    if not keep:
        return {}, gone
    new = copy.deepcopy(spec)
    new["spec"] = keep
    return new, gone


def _all(api, path):
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


def one(api, rec, questions, state, check_mode):
    r = {"template": rec.get("name") or "", "id": rec.get("id"), "playbook": rec.get("playbook") or "", "action": "",
         "added": [], "removed": [], "changed": False, "ok": False, "error": ""}
    if ((rec.get("summary_fields") or {}).get("user_capabilities") or {}).get("edit") is False:
        r["error"] = "the AAP user may not change this job template: give it the Admin role on it"
        return r
    path = "job_templates/%s/" % r["id"]
    code, spec = api.call("GET", path + "survey_spec/")
    if code != 200:
        r["error"] = "its survey could not be read (HTTP %d): %s" % (code, error_text(spec))
        return r
    enabled = bool(rec.get("survey_enabled"))
    writes = []
    if state == "present":
        new, r["added"] = add_questions(spec, questions)
        if new is not None:
            writes.append(("POST", path + "survey_spec/", new, "the survey"))
        if not enabled:
            writes.append(("PATCH", path, {"survey_enabled": True}, "turning the survey on"))
        r["action"] = (("added %d question(s)" % len(r["added"])) if r["added"] else "all questions already there") + (
            ", survey turned on" if not enabled else "")
    else:
        new, r["removed"] = remove_questions(spec, [q.get("variable") for q in questions])
        if new is None:
            r["action"] = "not there"
        elif new:
            writes.append(("POST", path + "survey_spec/", new, "the survey without the questions"))
            r["action"] = "removed %d question(s)" % len(r["removed"])
        else:
            writes.append(("DELETE", path + "survey_spec/", None, "removing the survey"))
            if enabled:
                writes.append(("PATCH", path, {"survey_enabled": False}, "turning the survey off"))
            r["action"] = "removed %d question(s) (none left: survey removed, turned off)" % len(r["removed"])
    if writes and not check_mode:
        for method, where, body, what in writes:
            code, d = api.call(method, where, body)
            if code not in (200, 201, 202, 204):
                r["error"] = "AAP refused %s (HTTP %d): %s" % (what, code, error_text(d))
                return r
    r["changed"] = bool(writes)
    r["ok"] = True
    return r


def run(api, p, check_mode):
    """(results, problems, missing playbooks)."""
    jobs, why = _all(api, "job_templates/")
    if why:
        return [], ["the job templates: " + why], []
    results, missing = [], []
    for s in p["surveys"]:
        pb = _base(s.get("playbook"))
        found = [j for j in jobs if _base(j.get("playbook")) == pb and (
            not p["project"] or (((j.get("summary_fields") or {}).get("project") or {}).get("name")) == p["project"])]
        if not found:
            missing.append(pb)
        for j in sorted(found, key=lambda x: (str(x.get("name")).lower(), x.get("id"))):
            try:
                results.append(one(api, j, s.get("questions") or [], p["state"], check_mode))
            except Exception as e:             # noqa: BLE001 - one template's surprise never stops the others
                results.append({"template": j.get("name") or "", "id": j.get("id"), "playbook": j.get("playbook") or "",
                                "action": "", "added": [], "removed": [], "changed": False, "ok": False,
                                "error": "unexpected answer from AAP: %s" % e})
    return results, [], missing


def main():
    module = AnsibleModule(argument_spec=dict(
        surveys=dict(type="list", elements="dict", required=True), project=dict(type="str", default=""),
        state=dict(type="str", choices=["present", "absent"], default="present"),
        ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)), supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="incident_docs_survey_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, problems=[why], results=[], missing=[])
    results, problems, missing = run(api, p, module.check_mode)
    module.exit_json(changed=any(r["changed"] for r in results), problems=problems, results=results, missing=missing)


if __name__ == "__main__":
    main()
