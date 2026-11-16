#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The finding a site portal fix job names, read back from the report job that found it (1.67.0 part C): the portal sends
a reference - the report job's id, the finding's id, the host - never the finding's values. This module reads that job's
site_findings artifact (plugins/filter/finding_filters.py makes it) and checks every part of the reference: the id's form,
that the job has it, that it is the host's, and that its kind is one this job takes. Read only. The address and login
come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_finding_ref
short_description: One finding of an earlier AAP report job (its site_findings artifact), by job, finding id and host
description:
  - Reads AAP job job_id and returns the finding finding_id of host from its site_findings artifact. Read only.
  - ok is false, with why, when AAP cannot be reached, the job cannot be read, the finding is not in it, it is another host's,
    or its kind is not one of kinds.
options:
  job_id: {description: The AAP job that reported the finding (report_job)., type: int, required: true}
  finding_id: {description: "The finding's id: <kind>:<host>:<key>.", type: str, required: true}
  host: {description: The host the fix runs on (target)., type: str, required: true}
  kinds: {description: The kinds this job takes ([] = any)., type: list, elements: str, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: The finding was found and is this host's, of an allowed kind., type: bool}
why: {description: Why not, when ok is false., type: str}
finding: {description: "The finding: {id, kind, host, title, severity, data}.", type: dict}
job: {description: "The report job: {id, name, finished, status}.", type: dict}
'''

import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

ID_CHARS = re.compile(r"^[A-Za-z0-9_.:/@-]+$")
ID_MAX = 200
NO_CREDENTIAL = ('no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user that can see the report job: Organization Auditor on its organization)')


def pick(artifact, job_id, finding_id, host, kinds=None):
    """(finding, why) - the finding finding_id of host in a job's site_findings ({host: [entry]}); why '' when it is."""
    fid, host = str(finding_id or ""), str(host or "")
    if not fid or len(fid) > ID_MAX or not ID_CHARS.match(fid):
        return {}, "the finding id %r is not one (letters, digits and _.:/@- only, at most %d)" % (fid[:80], ID_MAX)
    if not isinstance(artifact, dict):
        return {}, ("job %s has no site_findings artifact: it is not a report job with findings, or it ran before 1.67.0 - run "
                    "the report again and use its new page" % job_id)
    es = artifact.get(host)
    if not isinstance(es, list):
        return {}, "job %s reported nothing for %s" % (job_id, host)
    e = next((x for x in es if isinstance(x, dict) and x.get("id") == fid), None)
    if e is None:
        return {}, ("job %s has no finding %s for %s - fixed since, or another host's; open the report's newest page"
                    % (job_id, fid, host))
    if str(e.get("host")) != host or not fid.startswith("%s:" % e.get("kind")):
        return {}, "finding %s is not %s's" % (fid, host)
    if kinds and e.get("kind") not in kinds:
        return {}, "finding %s is a %s finding; this job takes only %s" % (fid, e.get("kind"), ", ".join(kinds))
    return dict((k, e.get(k)) for k in ("id", "kind", "host", "title", "severity", "data")), ""


def find(api, job_id, finding_id, host, kinds):
    """(finding, job, why)."""
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
    arts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    finding, why = pick(arts.get("site_findings"), job_id, finding_id, host, kinds)
    return finding, job, why


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), finding_id=dict(type="str", required=True),
                                              host=dict(type="str", required=True), kinds=dict(type="list", elements="str", default=[]),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="finding_ref_aap_ca_path", no_credential=NO_CREDENTIAL)
    try:
        finding, job, why = find(api, p["job_id"], p["finding_id"], p["host"], p["kinds"])
    except Exception as e:                                # noqa: BLE001 - said in why; the role stops the job with it
        finding, job, why = {}, {}, "reading job %d from AAP failed: %s: %s" % (p["job_id"], e.__class__.__name__, e)
    module.exit_json(changed=False, ok=not why, why=why, finding=finding, job=job,
                     msg=why or "finding %s of %s in job %d" % (p["finding_id"], p["host"], p["job_id"]))


if __name__ == "__main__":
    main()
