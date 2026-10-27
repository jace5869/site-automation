#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Put one execution environment on many AAP job templates at once - the templates of a project, those on a given
EE now, or named ones - through AAP's own API (playbooks/aap_ee.yml). The address and login come from the "Red Hat
Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py). Standard library only.
A template with no EE set (it uses the project's or organization's default) is changed only when asked for."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_ee
short_description: Put one execution environment on many AAP job templates
description:
  - Finds the job templates of O(project), and - O(from_ee) - those whose execution environment is one of these now,
    plus the ones named in O(templates); O(exclude) leaves some out. With none of O(project), O(from_ee) and
    O(templates) it changes nothing - never every template in AAP.
  - A template with no execution environment set uses its project's or organization's default. It is changed only
    when O(from_ee) has C(none) or it is named in O(templates); otherwise it is listed, left as it is.
  - Each one picked gets O(ee) (its name, or its ID when two have that name). One already on it is left as it is.
  - In check mode it only reads, and says what it would change. With a problem (a name in O(from_ee), O(templates) or
    O(exclude) that matches nothing, or a template name two have) it changes nothing either, and says what it would.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential
    sets it. Its user needs the Admin role on each template, and Use on the execution environment.
options:
  ee: {description: "The execution environment to put on the templates: its name in AAP, or its ID.", type: str, required: true}
  project: {description: "The job templates of this AAP project (its name); empty = any project.", type: str, default: ''}
  from_ee: {description: "Only the templates on one of these execution environments now (names or IDs); none = those with
    no execution environment set. Empty = any one set.", type: list, elements: str, default: []}
  templates: {description: "More job templates, by name or ID - whatever execution environment they have, or none.", type: list, elements: str, default: []}
  exclude: {description: "Templates left out, by name or ID.", type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
api: {description: The API path that answered., type: str, sample: api/controller/v2/}
ee: {description: "The execution environment put on the templates: id, name, image, pull, organization.", type: dict}
problems: {description: AAP could not be read, the execution environment or a template named is not there (or is two), or nothing was picked., type: list}
results:
  description: "Each template picked: its execution environment before, what was done - changed (written: true when it was,
    false in check mode or when there was a problem), already on it, left as it is, failed (error)."
  type: list
  sample: [{template: VM - health report, id: 12, project: Site automation, before: Minimal execution environment, action: changed,
            changed: true, ok: true, error: ""}]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user with the Admin role on the job '
                 'templates, and Use on the execution environment)')
NONE = "none"
NO_EE = "(none set: the project's or organization's default)"
PULL = {"always": "always pull", "missing": "pull only if not present", "never": "never pull", "": "the AAP default"}


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


def _org(rec):
    return ((rec.get("summary_fields") or {}).get("organization") or {}).get("name") or "every organization"


def find(records, name, what):
    """(the record named - by ID or name - or None, why not). Two with that name: name it by its ID."""
    name = str(name).strip()
    hits = [r for r in records if name in (str(r.get("id")), r.get("name"))]
    if not hits:
        return None, "no %s %s that the AAP user can see" % (what, name)
    if len(hits) > 1:
        return None, "%d %ss are named %s (%s): name it by its ID" % (
            len(hits), what, name, ", ".join("ID %s, %s" % (r.get("id"), _org(r)) for r in hits))
    return hits[0], ""


def ee_of(jt):
    """The template's execution environment now: (its ID or None, its name)."""
    i = jt.get("execution_environment")
    n = ((jt.get("summary_fields") or {}).get("execution_environment") or {}).get("name")
    return i, (n or ("ID %s" % i if i else NO_EE))


def pick(jobs, project="", from_ids=None, from_none=False, named_ids=(), exclude_ids=()):
    """[(template, why: 'picked' or 'unset')]: the templates of the project (any project when '' and from is given),
    those on from_ids (or with none set, from_none) when from is given; named_ids whatever they are on; not
    exclude_ids. Without from, a template with no EE set is 'unset' - listed, left as it is. Pure: testable on its own."""
    skip, named = set(exclude_ids or ()), set(named_ids or ())
    has_from = bool(from_ids) or from_none
    out = []
    for j in jobs or []:
        if j.get("id") in skip:
            continue
        if j.get("id") in named:
            out.append((j, "picked"))
            continue
        pj = ((j.get("summary_fields") or {}).get("project") or {}).get("name") or ""
        if not (project or has_from) or (project and pj != project):
            continue
        cur = j.get("execution_environment")
        if has_from:
            if (cur is None and from_none) or (cur is not None and cur in (from_ids or ())):
                out.append((j, "picked"))
        else:
            out.append((j, "picked" if cur is not None else "unset"))
    return out


def run(api, p, check_mode):
    """(the EE, results, problems). With a problem - a name that matches nothing, or two - nothing is written: the
    results say what would change (a misspelled exclude must not move the templates it was meant to keep)."""
    if not (str(p["project"]).strip() or p["from_ee"] or p["templates"]):
        return {}, [], ["which templates? set aap_ee_project (your site-automation project's name), aap_ee_from or aap_ee_templates "
                        "- this job never changes every template in AAP"]
    ees, why = _all(api, "execution_environments/")
    if why:
        return {}, [], ["the execution environments: " + why]
    target, why = find(ees, p["ee"], "execution environment")
    if why:
        return {}, [], ["aap_ee_name: " + why + " (Automation Execution > Infrastructure > Execution Environments)"]
    ee = {"id": target.get("id"), "name": target.get("name") or "", "image": target.get("image") or "",
          "pull": PULL.get(target.get("pull") or "", target.get("pull")), "organization": _org(target)}
    problems, from_ids, from_none = [], set(), False
    for f in p["from_ee"]:
        if str(f).strip().lower() == NONE:
            from_none = True
            continue
        rec, why = find(ees, f, "execution environment")
        if why:
            problems.append("aap_ee_from: " + why)
        else:
            from_ids.add(rec.get("id"))
    jobs, why = _all(api, "job_templates/")
    if why:
        return ee, [], problems + ["the job templates: " + why]
    named_ids, exclude_ids = set(), set()
    for t in p["templates"]:
        rec, why = find(jobs, t, "job template")
        if why:
            problems.append("aap_ee_templates: " + why)
        else:
            named_ids.add(rec.get("id"))
    for x in p["exclude"]:
        hits = [j.get("id") for j in jobs if str(x).strip() in (str(j.get("id")), j.get("name"))]
        if hits:
            exclude_ids.update(hits)               # two with that name: both left out
        else:
            problems.append("aap_ee_exclude: no job template %s that the AAP user can see - check its name" % str(x).strip())
    if p["project"] and not any(((j.get("summary_fields") or {}).get("project") or {}).get("name") == p["project"] for j in jobs):
        problems.append("aap_ee_project: no job template of a project named %s that the AAP user can see" % p["project"])
    write = not check_mode and not problems
    results = []
    for jt, why in sorted(pick(jobs, p["project"], from_ids, from_none, named_ids, exclude_ids),
                          key=lambda x: (str(x[0].get("name")).lower(), x[0].get("id"))):
        cur, cur_name = ee_of(jt)
        r = {"template": jt.get("name") or "", "id": jt.get("id"), "before": cur_name, "action": "", "changed": False, "written": False,
             "ok": True, "error": "",
             "project": ((jt.get("summary_fields") or {}).get("project") or {}).get("name") or ""}
        if why == "unset":
            r["action"] = "left as it is - no execution environment set, it uses the project's or organization's default (aap_ee_from: [none] sets it too)"
        elif cur == ee["id"]:
            r["action"] = "already on it"
        elif ((jt.get("summary_fields") or {}).get("user_capabilities") or {}).get("edit") is False:
            r.update(ok=False, error="the AAP user may not change this job template: give it the Admin role on it")
        else:
            r.update(action="%s -> %s" % (cur_name, ee["name"]), changed=True)
            if write:
                code, d = api.call("PATCH", "job_templates/%s/" % jt.get("id"), {"execution_environment": ee["id"]})
                if code in (200, 201, 202, 204):
                    r["written"] = True
                else:
                    r.update(ok=False, changed=False, error="AAP refused the change (HTTP %d): %s" % (code, error_text(d)))
        results.append(r)
    return ee, results, problems


def main():
    module = AnsibleModule(argument_spec={
        "ee": dict(type="str", required=True), "project": dict(type="str", default=""),
        "from_ee": dict(type="list", elements="str", default=[]), "templates": dict(type="list", elements="str", default=[]),
        "exclude": dict(type="list", elements="str", default=[]), "ca_path": dict(type="str", default=""),
        "timeout": dict(type="int", default=30)}, supports_check_mode=True)
    p = module.params
    if not str(p["ee"] or "").strip():
        module.fail_json(msg="aap_ee_name: the execution environment to put on the templates (its name in AAP, or its ID)")
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_ee_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, api=api.base, ee={}, problems=[why], results=[])
    ee, results, problems = run(api, p, module.check_mode)
    module.exit_json(changed=any(r["changed"] for r in results), api=api.base, ee=ee, problems=problems, results=results)


if __name__ == "__main__":
    main()
