#!/usr/bin/python
# -*- coding: utf-8 -*-
"""This job template's last runs, from AAP's own API, for the strip of past runs at the top of a report
(roles/site_email, report_email_theme: command). Each run: when it finished, how it ended, and - for a run
that sent a report - the report's status and headline number (its job artifact site_report_run). The address
and login come from the "Red Hat Ansible Automation Platform" credential's variables
(plugins/module_utils/site_aap.py). Read only. Never fails the job: without AAP it says why, and the report
goes out without the strip. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_report_history
short_description: A job template's last runs, from AAP, for a report's strip of past runs
description:
  - Lists the finished jobs of one job template before this one (newest first from AAP, returned oldest
    first), then reads each one's artifacts for site_report_run (the report it sent).
  - Read only, also in check mode. Never fails - ok is false and why says what was missing.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform"
    credential sets it (CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and
    CONTROLLER_PASSWORD, CONTROLLER_VERIFY_SSL, or the AAP_* / TOWER_* names). Its user must be able to
    see the template's jobs - Organization Auditor on the template's organization (or any role on the template).
options:
  job_id: {description: This job's ID (awx_job_id) - only runs before it are listed., type: int, required: true}
  template_id: {description: Its job template's ID (awx_job_template_id)., type: int, required: true}
  runs: {description: How many past runs., type: int, default: 13}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: AAP answered., type: bool}
why: {description: Why there are no past runs, when ok is false., type: str}
runs:
  description: The past runs, oldest first. report is the job's site_report_run artifact, or null (an older run, or one that sent no report).
  type: list
  sample: [{id: 4180, status: successful, finished: "2026-10-21T06:01:12.5Z", report: {status: ok, value: 3, label: Deleted}}]
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Organization Auditor on its organization)')
DONE = "successful,failed,error,canceled"


def history(api, job_id, template_id, runs):
    """(runs oldest first, why): why is '' when AAP answered."""
    why = api.probe()
    if why:
        return [], why
    code, data = api.call("GET", "jobs/?job_template=%d&id__lt=%d&status__in=%s&order_by=-id&page_size=%d"
                          % (template_id, job_id, DONE, max(1, min(runs, 50))))
    if code != 200:
        return [], ("AAP refused the list of this template's jobs (HTTP %d): %s - its user needs Organization Auditor on the template's organization"
                    % (code, error_text(data)) if code in (401, 403) else
                    "AAP answered HTTP %d for this template's jobs: %s" % (code, error_text(data)))
    out = []
    for j in (data.get("results") or [])[:runs]:
        if not isinstance(j, dict) or not isinstance(j.get("id"), int):
            continue
        report = None
        c, d = api.call("GET", "jobs/%d/" % j["id"])      # the list leaves the artifacts out
        art = d.get("artifacts") if c == 200 else None
        if isinstance(art, dict) and isinstance(art.get("site_report_run"), dict):
            r = art["site_report_run"]
            report = {"status": str(r.get("status") or ""), "value": r.get("value"), "label": str(r.get("label") or "")}
        out.append({"id": j["id"], "status": str(j.get("status") or ""), "finished": str(j.get("finished") or ""),
                    "report": report})
    return list(reversed(out)), ""


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), template_id=dict(type="int", required=True),
                                              runs=dict(type="int", default=13), ca_path=dict(type="str", default=""),
                                              timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    runs, why = history(api, p["job_id"], p["template_id"], p["runs"])
    module.exit_json(changed=False, ok=not why, why=why, runs=runs,
                     msg=why or "%d past run(s) of this template from AAP" % len(runs))


if __name__ == "__main__":
    main()
