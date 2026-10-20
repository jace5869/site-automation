#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Replace the choices of one multiple-choice question in the survey of AAP job templates or workflow
job templates, through AAP's own API: the pick lists (vm_pick_refresh.yml) write what may be restarted
or deleted right now, so whoever launches the template picks from a current list. The address and login
come from the "Red Hat Ansible Automation Platform" credential's variables (module_utils/site_aap.py).
Standard library only. Never creates a template, a survey or a question: a name that is not there is
refused, never guessed. The rest of the survey is left as it is."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_survey
short_description: Replace the choices of a multiple-choice survey question in AAP
description:
  - For each target, finds the job template or workflow job template (by name or ID), reads its survey
    (survey_spec), finds the question by its variable name, and replaces its choices. The other
    questions, and the question's own text and settings, are kept. A default that is no longer one of
    the choices is dropped.
  - The question must be Multiple choice (single select) or Multiple choice (multiple select);
    multiselect=true makes a single-select one multiple select. Anything else is refused.
  - No choices at all - AAP needs at least one - writes empty_choice instead, which the pick jobs ignore.
  - In check mode it only reads, and says what it would change.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform"
    credential sets it (CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and
    CONTROLLER_PASSWORD, CONTROLLER_VERIFY_SSL, or the AAP_* / TOWER_* names). Its user needs the Admin
    role on each template.
options:
  targets:
    description: "[{template: name or ID, kind: job, workflow or empty (either - a name both kinds have is refused),
      question: the variable name, choices: [text], multiselect: true, note: text, empty_choice: text}]. note
      goes at the end of the question's description, in brackets, replacing the note an earlier run put there."
    type: list
    elements: dict
    required: true
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
api: {description: The API path that answered., type: str, sample: api/controller/v2/}
problems: {description: AAP could not be reached or refused the login (then nothing was read or written)., type: list}
results:
  description: Each target - the template found, how many choices it has now, whether they changed, and why not when it failed.
  type: list
  sample: [{template: VM - restart picked VMs, kind: job, id: 52, question: vm_reboot_picks, choices: 3, changed: true, ok: true,
            error: "", warnings: []}]
'''

import copy
import re
from urllib.parse import quote

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the templates '
                 'whose survey it fills)')
KINDS = {"job": ("job_templates", "job template"), "workflow": ("workflow_job_templates", "workflow job template")}
CHOICE_TYPES = ("multiplechoice", "multiselect")
NOTE = re.compile(r"\s*\[pick list[^\[\]]*\]\s*$")


def _list(value):
    """A question's choices or default as a list: AAP keeps them as a list, or (older AAP) as text, one per line."""
    if isinstance(value, list):
        return [str(x) for x in value]
    return [x for x in str(value or "").split("\n") if x.strip()]


def _like(value, items):
    """items written the way value was: text one per line (older AAP), else a list."""
    return "\n".join(items) if isinstance(value, str) else list(items)


def set_choices(spec, question, choices, multiselect=True, note="", empty_choice=""):
    """The survey with the question's choices replaced. Pure: no AAP, so it can be tested on its own.
    -> (new survey, error text or '', [what changed]); the survey is unchanged when there is an error."""
    spec = copy.deepcopy(spec or {})
    qs = spec.get("spec") or []
    q = next((x for x in qs if isinstance(x, dict) and x.get("variable") == question), None)
    if q is None:
        have = ", ".join(str(x.get("variable")) for x in qs if isinstance(x, dict)) or "none"
        return spec, "its survey has no question whose variable is %s (its questions: %s): add it (Survey > Add question)" % (question, have), []
    kind = str(q.get("type") or "")
    if kind not in CHOICE_TYPES:
        return spec, ("the question %s is a %s question: make it Multiple choice (multiple select), the job writes its choices"
                      % (question, kind or "?")), []
    out, seen = [], set()
    for c in choices or []:
        c = " ".join(str(c).split())             # one line: a choice is one line of the list
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    if not out and empty_choice:
        out = [" ".join(str(empty_choice).split())]
    if not out:
        return spec, "nothing to offer, and no empty_choice: AAP needs at least one choice", []
    changes = []
    if multiselect and kind == "multiplechoice":
        q["type"] = "multiselect"
        changes.append("made multiple select")
    old = q.get("choices")
    if _list(old) != out:
        q["choices"] = _like(old, out)
        changes.append("%d choice(s)" % len(out))
    if "default" in q:
        keep = [d for d in _list(q["default"]) if d in out]
        if q["type"] == "multiplechoice":
            keep = keep[:1]
        new_default = _like(q["default"], keep)
        if new_default != q["default"]:
            q["default"] = new_default
            changes.append("default cleared" if not keep else "default kept to the choices still offered")
    if note:
        desc = NOTE.sub("", str(q.get("question_description") or ""))
        new_desc = ((desc + " ") if desc else "") + "[pick list %s]" % " ".join(str(note).split())
        if new_desc != q.get("question_description"):
            q["question_description"] = new_desc
            if not changes:
                changes.append("note")
    return spec, "", changes


def find(api, target, kind):
    """(the template's record, its kind, why not)."""
    name = str(target).strip()
    kinds = [kind] if kind else ["job", "workflow"]
    found = []
    for k in kinds:
        path = KINDS[k][0]
        if name.isdigit():
            code, d = api.call("GET", "%s/%s/" % (path, name))
            hits = [d] if code == 200 else []
            if code not in (200, 404):
                return None, k, "%s %s could not be read (HTTP %d): %s" % (KINDS[k][1], name, code, error_text(d))
        else:
            code, d = api.call("GET", "%s/?name=%s" % (path, quote(name)))
            if code != 200:
                return None, k, "the %ss could not be read (HTTP %d): %s" % (KINDS[k][1], code, error_text(d))
            hits = d.get("results") or []
        found += [(h, k) for h in hits]
    what = " or ".join(KINDS[k][1] for k in kinds)
    if not found:
        return None, kind, "no %s %s that the AAP user can see: check the name, or give the user the Admin role on it" % (what, name)
    if len(found) > 1:
        both = ", ".join("%s %s" % (KINDS[k][1], h.get("id")) for h, k in found)
        return None, kind, ("%d templates are named %s (%s): name it by its ID, the number in its address in AAP, and say which kind "
                            "(kind: job or workflow)" % (len(found), name, both))
    rec, k = found[0]
    if ((rec.get("summary_fields") or {}).get("user_capabilities") or {}).get("edit") is False:
        return None, k, "the AAP user may not change %s %s: give it the Admin role on it" % (KINDS[k][1], rec.get("name", name))
    return rec, k, ""


def one(api, t, check_mode):
    r = {"template": str(t.get("template") or ""), "kind": str(t.get("kind") or ""), "id": None, "question": str(t.get("question") or ""),
         "choices": 0, "changed": False, "ok": False, "error": "", "warnings": [], "changes": []}
    if r["kind"] and r["kind"] not in KINDS:
        r["error"] = "kind is %s: job, workflow, or empty for either" % r["kind"]
        return r
    if not r["template"] or not r["question"]:
        r["error"] = "a template and a question (its variable name) are needed"
        return r
    rec, kind, why = find(api, r["template"], r["kind"])
    r["kind"] = kind or r["kind"]
    if why:
        r["error"] = why
        return r
    r["id"], r["template"] = rec.get("id"), rec.get("name") or r["template"]
    path = "%s/%s/survey_spec/" % (KINDS[kind][0], r["id"])
    code, spec = api.call("GET", path)
    if code != 200:
        r["error"] = "its survey could not be read (HTTP %d): %s" % (code, error_text(spec))
        return r
    if not (spec or {}).get("spec"):
        r["error"] = "it has no survey: add one (the template > Survey) with the question %s, Multiple choice (multiple select)" % r["question"]
        return r
    new, why, changes = set_choices(spec, r["question"], t.get("choices") or [], t.get("multiselect", True) is not False,
                                    t.get("note") or "", t.get("empty_choice") or "")
    if why:
        r["error"] = why
        return r
    q = next(x for x in new["spec"] if x.get("variable") == r["question"])
    r["choices"] = len(_list(q.get("choices")))
    if not rec.get("survey_enabled", True):
        r["warnings"].append("the survey of %s %s is turned off: turn it on (Survey > the switch), or the list is never asked"
                             % (KINDS[kind][1], r["template"]))
    r["changes"] = changes
    if changes and not check_mode:
        code, d = api.call("POST", path, new)
        if code not in (200, 201, 204):
            r["error"] = "AAP refused the new survey (HTTP %d): %s" % (code, error_text(d))
            return r
    r["changed"] = bool(changes)
    r["ok"] = True
    return r


def main():
    module = AnsibleModule(argument_spec=dict(targets=dict(type="list", elements="dict", required=True),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="vm_pick_aap_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, api=api.base, problems=[why], results=[])
    results = []
    for t in p["targets"]:
        try:
            results.append(one(api, t, module.check_mode))
        except Exception as e:                  # noqa: BLE001 - one template's surprise never stops the others
            results.append({"template": str(t.get("template") or ""), "kind": str(t.get("kind") or ""), "id": None,
                            "question": str(t.get("question") or ""), "choices": 0, "changed": False, "ok": False,
                            "error": "unexpected answer from AAP: %s" % e, "warnings": [], "changes": []})
    module.exit_json(changed=any(r["changed"] for r in results), api=api.base, problems=[], results=results)


if __name__ == "__main__":
    main()
