#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Write the survey questions of a job into the AAP job templates that run its playbook - the outage AAR and SOP jobs
(playbooks/incident_docs_survey.yml) - through AAP's own API, so nobody types twenty questions by hand. The address and
login come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py).
Standard library only. A question already there (its variable) is left as it is - your own wording, choices or
defaults stay; the survey's other questions are never changed. It also writes the defaults of questions that are
there (the outage AAR job's SOP draft: the SOP job's form filled in, to review before it is launched - and cleared
again by the SOP job), and can attach a credential to the templates (the setup job: the one the draft needs)."""
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
  - An entry's C(defaults) ({variable: text}) sets the default answer of those questions, already in the survey (a
    text longer than the question's maximum is cut, and said); C(match) ({variable: text}) writes them only when the
    question's default is that now (the SOP job clears the draft only when it is the one it was launched from).
  - O(credential) attaches that credential (its name) to each template found, unless the template already has one
    of its type.
  - In check mode it only reads, and says what it would change.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential sets it.
    Its user needs the Admin role on each template.
options:
  surveys:
    description: "[{playbook: incident_aar.yml, questions: [AAP survey questions: variable, question_name, type ...],
      defaults: {variable: text}, match: {variable: text}}]"
    type: list
    elements: dict
    required: true
  project: {description: "Only job templates of this AAP project (its name); empty = any project.", type: str, default: ''}
  credential: {description: "A credential to attach to the templates (its name in AAP); empty = none.", type: str, default: ''}
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
            added: [incident_name], defaults: [], cut: [], changed: true, ok: true, error: ""}]
missing: {description: The playbooks no job template runs (make the template first)., type: list}
'''

import copy
import os
from urllib.parse import quote

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


def set_defaults(spec, defaults, match=None):
    """(the new survey or None when nothing changes, [the variables set], [those cut to the question's maximum],
    [the variables the survey has no question for], why nothing was written ('' or the match that failed)). Pure."""
    spec = copy.deepcopy(spec or {})
    qs = dict((str(q.get("variable")), q) for q in spec.get("spec") or [])
    for k, v in sorted((match or {}).items()):
        if k not in qs or str(qs[k].get("default") or "") != str(v or ""):
            return None, [], [], [], "the form's %s is not %r now" % (k, v)
    done, cut, missing = [], [], []
    for k, v in (defaults or {}).items():
        if k not in qs:
            missing.append(k)
            continue
        q, text = qs[k], str(v if v is not None else "")
        most = q.get("max")
        if q.get("type") in ("text", "textarea") and isinstance(most, int) and most > 0 and len(text) > most:
            text = text[:most].rstrip()
            cut.append(k)
        if q.get("type") in ("multiplechoice", "multiselect"):
            continue                                   # a pick list keeps its own default
        if str(q.get("default") or "") != text:
            q["default"] = text
            done.append(k)
    return (spec if done else None), done, cut, missing, ""


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


def attach(api, rec, cred, check_mode):
    """(what was done, error): the credential attached to the template, unless it has one of that type."""
    path = "job_templates/%s/credentials/" % rec.get("id")
    have, why = _all(api, path)
    if why:
        return "", "its credentials: " + why
    same = [c for c in have if c.get("id") == cred.get("id") or c.get("credential_type") == cred.get("credential_type")]
    if same:
        return ("credential '%s' already there" % same[0].get("name")) if same[0].get("id") == cred.get("id") else (
            "has '%s' of that credential type already - left as it is" % same[0].get("name")), ""
    if not check_mode:
        code, d = api.call("POST", path, {"id": cred.get("id")})
        if code not in (200, 201, 204):
            return "", "AAP refused to attach credential '%s' (HTTP %d): %s" % (cred.get("name"), code, error_text(d))
    return "credential '%s' attached" % cred.get("name"), ""


def one(api, rec, questions, state, check_mode, defaults=None, match=None, cred=None):
    r = {"template": rec.get("name") or "", "id": rec.get("id"), "playbook": rec.get("playbook") or "", "action": "",
         "added": [], "removed": [], "defaults": [], "cut": [], "no_question": [], "changed": False, "ok": False, "error": ""}
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
            ", survey turned on" if not enabled else "") if questions else ""
        if defaults:
            base = new if new is not None else spec
            dnew, r["defaults"], r["cut"], r["no_question"], skip = set_defaults(base, defaults, match)
            if dnew is not None:
                if new is not None:
                    writes[0] = ("POST", path + "survey_spec/", dnew, "the survey")
                else:
                    writes.append(("POST", path + "survey_spec/", dnew, "the survey's default answers"))
            said = skip or (("%d default answer(s) written" % len(r["defaults"])) if r["defaults"] else "the default answers already so")
            r["action"] = ", ".join(x for x in (r["action"], said) if x)
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
    if cred:
        did, why = attach(api, rec, cred, check_mode)
        if why:
            r["error"] = why
            return r
        r["action"] = ", ".join(x for x in (r["action"], did) if x)
        r["changed"] = "attached" in did
    if writes and not check_mode:
        for method, where, body, what in writes:
            code, d = api.call(method, where, body)
            if code not in (200, 201, 202, 204):
                r["error"] = "AAP refused %s (HTTP %d): %s" % (what, code, error_text(d))
                return r
    r["changed"] = r["changed"] or bool(writes)
    r["ok"] = True
    return r


def run(api, p, check_mode):
    """(results, problems, missing playbooks)."""
    jobs, why = _all(api, "job_templates/")
    if why:
        return [], ["the job templates: " + why], []
    cred = None
    if p.get("credential"):
        found, why = _all(api, "credentials/?name=%s" % quote(p["credential"]))
        found = [c for c in found if c.get("name") == p["credential"]]
        if why or not found:
            return [], ["the credential '%s' (incident_docs_draft_credential): %s" % (
                p["credential"], why or "not there, or this job's AAP user may not use it")], []
        cred = found[0]
    results, missing = [], []
    for s in p["surveys"]:
        pb = _base(s.get("playbook"))
        found = [j for j in jobs if _base(j.get("playbook")) == pb and (
            not p["project"] or (((j.get("summary_fields") or {}).get("project") or {}).get("name")) == p["project"])]
        if not found:
            missing.append(pb)
        for j in sorted(found, key=lambda x: (str(x.get("name")).lower(), x.get("id"))):
            try:
                results.append(one(api, j, s.get("questions") or [], p["state"], check_mode, s.get("defaults"), s.get("match"), cred))
            except Exception as e:             # noqa: BLE001 - one template's surprise never stops the others
                results.append({"template": j.get("name") or "", "id": j.get("id"), "playbook": j.get("playbook") or "",
                                "action": "", "added": [], "removed": [], "defaults": [], "cut": [], "no_question": [],
                                "changed": False, "ok": False,
                                "error": "unexpected answer from AAP: %s" % e})
    return results, [], missing


def main():
    module = AnsibleModule(argument_spec=dict(
        surveys=dict(type="list", elements="dict", required=True), project=dict(type="str", default=""),
        credential=dict(type="str", default=""),
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
