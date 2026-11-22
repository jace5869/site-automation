#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read an outage review's AAR draft back from AAP (1.112.0): the "VM - outage watch" run that made the review keeps it in
its job artifact vm_outage_review.aar. The AAR job (incident_review: a job number, or latest) fills its empty boxes from
it. Read only: GET requests, through the "Red Hat Ansible Automation Platform" credential (plugins/module_utils/site_aap.py)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_outage_review_read
short_description: Read an outage review's AAR draft from its AAP job (read only)
description:
  - O(review) is an AAP job number, or C(latest) - the newest job of a template that runs playbooks/vm_outage_watch.yml
    whose artifact has a review's draft (vm_outage_review.aar: a run launched with vm_watch_review). Up to O(look_back)
    of the newest such jobs are read.
  - GET only. AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential
    sets it; its user needs to see the outage watch's jobs (the Read role on that template is enough).
options:
  review: {description: A job number or latest., type: str, required: true}
  look_back: {description: The newest outage watch jobs read at most for latest., type: int, default: 25}
  playbook: {description: The outage watch's playbook (its file name)., type: str, default: vm_outage_watch.yml}
  project: {description: Only jobs of this AAP project (its name); empty = any project., type: str, default: ''}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
ok: {description: A draft was read., type: bool}
job: {description: The job it came from (its number)., type: str}
job_name: {description: That job's name., type: str}
draft: {description: The draft (incident_doc_filters.incident_review_draft's shape); {} when none., type: dict}
error: {description: Why nothing was read ('' when ok)., type: str}
'''

import os
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to the AAR job template '
                 '(incident_docs_draft_credential; its user needs to see the VM - outage watch jobs: the Read role on that template)')


def _base(path):
    return os.path.basename(str(path or "").strip().replace("\\", "/"))


def draft_of(job):
    """A job's draft ({} when it has none) - its artifact vm_outage_review.aar."""
    art = (job or {}).get("artifacts")
    rv = art.get("vm_outage_review") if isinstance(art, dict) else None
    d = rv.get("aar") if isinstance(rv, dict) else None
    return d if isinstance(d, dict) and d.get("v") else {}


def _project(job):
    return str((((job or {}).get("summary_fields") or {}).get("project") or {}).get("name") or "")


def _not_this_project(job, project):
    """'' when the job is of project (or none is set); else why not - also when AAP does not say its project."""
    if not project:
        return ""
    have = _project(job)
    if not have:
        return "names no project, so it cannot be checked against %s (incident_docs_survey_project)" % project
    return "" if have == project else "is of project %s, not %s (incident_docs_survey_project)" % (have, project)


def _get(api, path):
    """GET, once more after no answer or a 5xx (a large artifact cut off on its way, a busy AAP)."""
    code, data = api.call("GET", path)
    if code == -1 or code >= 500:
        code, data = api.call("GET", path)
    return code, data


def read(api, review, playbook="vm_outage_watch.yml", look_back=25, project=""):
    """-> {ok, job, job_name, draft, error}. latest never falls back to an older review: a run it cannot read is said."""
    want = str(review or "").strip().lower()
    out = {"ok": False, "job": "", "job_name": "", "draft": {}, "error": ""}
    if re.fullmatch(r"[0-9]+", want):              # ASCII digits only (a "²" is not a job number)
        code, job = _get(api, "jobs/%s/" % want)
        if code == 404:
            out["error"] = "AAP has no job %s (or this credential's user may not see it)" % want
            return out
        if code != 200:
            out["error"] = "job %s could not be read (HTTP %d): %s" % (want, code, error_text(job))
            return out
        out["job"], out["job_name"] = want, str(job.get("name") or "")
        # 1.112.0 (Codex P1): only the outage watch's own job, of this project when one is set - never a look-alike artifact
        if _base(job.get("playbook")) != playbook:
            out["error"] = "job %s (%s) is not a VM - outage watch run (playbooks/%s)" % (want, out["job_name"] or "?", playbook)
            return out
        why = _not_this_project(job, project)
        if why:
            out["error"] = "job %s %s" % (want, why)
            return out
        d = draft_of(job)
        if not d:
            out["error"] = ("job %s (%s) has no outage review to fill from: launch VM - outage watch with vm_watch_review: true "
                            "(1.112.0 or later), then use that job's number" % (want, out["job_name"] or _base(job.get("playbook"))))
            return out
        out.update(ok=True, draft=d)
        return out
    if want != "latest":
        out["error"] = "incident_review is %r: give an AAP job number (e.g. 4714) or latest" % review
        return out
    most = max(1, int(look_back))
    path = "jobs/?playbook__endswith=%s&order_by=-id&page_size=%d" % (playbook, min(most, 200))     # AAP's pages hold 200 at most
    jobs = []
    while path and len(jobs) < most:
        code, d = _get(api, path)
        if code != 200:
            out["error"] = "the outage watch's jobs could not be listed (HTTP %d): %s" % (code, error_text(d))
            return out
        jobs += [j for j in d.get("results") or [] if _base(j.get("playbook")) == playbook
                 and (not project or not _project(j) or _project(j) == project)]
        nxt = d.get("next")
        path = (nxt.split(api.base, 1)[1] if api.base and api.base in nxt else str(nxt).lstrip("/")) if nxt else ""
    jobs = jobs[:most]
    for j in jobs:
        code, job = _get(api, "jobs/%s/" % j.get("id"))
        if code != 200:                        # never an older outage's review instead (Neon P1): say which run could not be read
            out["error"] = ("outage watch job %s could not be read (HTTP %d): %s. latest stops there rather than take an older "
                            "review: try again, or give a job number" % (j.get("id"), code, error_text(job)))
            out["job"] = str(j.get("id"))
            return out
        why = _not_this_project(job, project)
        if why and _project(job):
            continue                           # another project's outage watch: not ours, the next one
        if why:                                # no project said: never taken as a match - stop, said
            out["error"] = "outage watch job %s %s" % (j.get("id"), why)
            out["job"] = str(j.get("id"))
            return out
        dr = draft_of(job)
        if dr:
            out.update(ok=True, job=str(j.get("id")), job_name=str(job.get("name") or ""), draft=dr)
            return out
    out["error"] = ("no outage review among the newest %d VM - outage watch job(s) this credential's user can see: launch it with "
                    "vm_watch_review: true, or give the job number" % len(jobs)) if jobs else (
        "no VM - outage watch job (playbooks/%s) that this credential's user can see" % playbook)
    return out


def main():
    module = AnsibleModule(argument_spec=dict(review=dict(type="str", required=True), look_back=dict(type="int", default=25),
                                              playbook=dict(type="str", default="vm_outage_watch.yml"), project=dict(type="str", default=""),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="incident_docs_survey_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, ok=False, job="", job_name="", draft={}, error=why)
    module.exit_json(changed=False, **read(api, p["review"], p["playbook"], p["look_back"], p["project"]))


if __name__ == "__main__":
    main()
