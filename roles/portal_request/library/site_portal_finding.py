#!/usr/bin/python
# -*- coding: utf-8 -*-
"""One finding of an earlier AAP job, for "ACT - triage finding" (workflow 44, 1.67.0): the site portal sends a
reference (the job's id, the host and the finding's id), never the finding's text. This module reads that job's
site_health artifact (roles/site_findings/tasks/report.yml publishes it) and returns the finding as the check wrote
it - so a finding cannot be made up on the way. Read only. The address and login come from the "Red Hat Ansible
Automation Platform" credential's variables (plugins/module_utils/site_aap.py). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_portal_finding
short_description: One finding of an earlier AAP job (its site_health artifact), by job id, host and finding id
description:
  - Reads AAP job job_id and returns the finding finding_id that host had in its site_health artifact. Read only.
  - ok is false, with why, when AAP cannot be reached, the job cannot be read, or the host or finding is not in it.
options:
  job_id: {description: The AAP job that reported the finding., type: int, required: true}
  host: {description: The host, as the job's report names it., type: str, required: true}
  finding_id: {description: The finding's id (fapolicyd:denials, eventlog:disk-errors ...)., type: str, required: true}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: The finding was found., type: bool}
why: {description: Why not, when ok is false., type: str}
finding: {description: The finding as the check wrote it (check, id, severity, summary, hint)., type: dict}
job: {description: The job (id, name, finished, status)., type: dict}
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user that can see the job that reported the finding: Organization Auditor on its organization)')


def find(api, job_id, host, finding_id):
    """(finding, job, why): why is '' when the finding was found."""
    why = api.probe()
    if why:
        return {}, {}, why
    code, data = api.call("GET", "jobs/%d/" % job_id)
    if code == -1:                                    # cut off on its way (a job with large artifacts): once more
        code, data = api.call("GET", "jobs/%d/" % job_id)
    if code in (403, 404):
        return {}, {}, ("AAP job %d cannot be read (HTTP %d): it does not exist, or %s cannot see it - give that user "
                        "Organization Auditor on the job's organization" % (job_id, code, api.user or "the credential's AAP user"))
    if code != 200 or not isinstance(data, dict):
        return {}, {}, "AAP answered HTTP %d for job %d: %s" % (code, job_id, error_text(data))
    job = {"id": job_id, "name": str(data.get("name") or ""), "finished": str(data.get("finished") or ""),
           "status": str(data.get("status") or "")}
    health = (data.get("artifacts") or {}).get("site_health") if isinstance(data.get("artifacts"), dict) else None
    if not isinstance(health, dict):
        return {}, job, ("job %d (%s) has no site_health artifact: it is not a health or fapolicyd check job, or it ran "
                         "before its report was published" % (job_id, job["name"] or "no name"))
    rec = health.get(host)
    if not isinstance(rec, dict):
        return {}, job, "job %d (%s) has no result for %s" % (job_id, job["name"], host)
    for f in rec.get("findings") or []:
        if isinstance(f, dict) and str(f.get("id") or "") == finding_id:
            keep = dict((k, f.get(k)) for k in ("check", "id", "severity", "summary", "hint") if f.get(k) is not None)
            return keep, job, ""
    return {}, job, ("job %d (%s) has no finding %s for %s - it may have been fixed since, or the report is older than "
                     "the job's artifacts" % (job_id, job["name"], finding_id, host))


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), host=dict(type="str", required=True),
                                              finding_id=dict(type="str", required=True), ca_path=dict(type="str", default=""),
                                              timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="portal_request_aap_ca_path", no_credential=NO_CREDENTIAL)
    try:
        finding, job, why = find(api, p["job_id"], p["host"], p["finding_id"])
    except Exception as e:                                # noqa: BLE001 - said in why; the playbook stops with it
        finding, job, why = {}, {}, "reading job %d from AAP failed: %s: %s" % (p["job_id"], e.__class__.__name__, e)
    module.exit_json(changed=False, ok=not why, why=why, finding=finding, job=job,
                     msg=why or "finding %s of %s in job %d" % (p["finding_id"], p["host"], p["job_id"]))


if __name__ == "__main__":
    main()
