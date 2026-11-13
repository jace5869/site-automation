#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The capacity trends report's earlier points, from AAP's own job history (playbooks/ontap_capacity_trends.yml,
roles/ontap_report/tasks/capacity.yml). Every run of the report keeps what it read as the job artifact ontap_capacity
(one point: each cluster's, aggregate's and volume's size and used space). This reads the artifacts of the same job
template's earlier jobs back - as far as ontap_capacity_days, and as far as AAP keeps its jobs (the management job
"Cleanup Job Details", 120 days unless changed). The address and login come from the "Red Hat Ansible Automation
Platform" credential's variables (plugins/module_utils/site_aap.py). Read only. Never fails the job: without AAP it says
why, and the report shows this run's point alone (and the report database's, when one is set up). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ontap_capacity_history
short_description: The capacity trends report's earlier points, from its job template's earlier jobs in AAP
description:
  - Lists the finished jobs of one job template before this one, newest first, until one finished before the window
    (days), then reads each one's artifacts for ontap_capacity (the point that run kept).
  - A job in skip_jobs is not read (its point is already at hand - from the report database).
  - Read only, also in check mode. Never fails - ok is false and why says what was missing.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential sets it
    (CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and CONTROLLER_PASSWORD, CONTROLLER_VERIFY_SSL, or
    the AAP_* / TOWER_* names). Its user must be able to see the template's jobs - Organization Auditor on the template's
    organization (or any role on the template).
options:
  job_id: {description: This job's ID (awx_job_id) - only runs before it are read., type: int, required: true}
  template_id: {description: Its job template's ID (awx_job_template_id)., type: int, required: true}
  days: {description: How far back (ontap_capacity_days)., type: float, default: 30}
  max_runs: {description: Read at most this many jobs (ontap_capacity_runs_max)., type: int, default: 400}
  skip_jobs: {description: Job IDs whose point is already at hand., type: list, elements: int, default: []}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: AAP answered., type: bool}
why: {description: Why there are no earlier points, when ok is false., type: str}
runs:
  description: The earlier runs' points, oldest first - each the job's ontap_capacity artifact with the job's ID (job).
  type: list
  sample: [{job: 4180, v: 1, read_at: "2026-10-21T06:00:05Z", clusters: [{uuid: "...", name: cluster1, size_b: 1, used_b: 1}]}]
jobs: {description: "How many jobs were listed, read, skipped (skip_jobs) and had no point (failed before it, or an older version).", type: dict}
'''

import calendar
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Organization Auditor on its organization)')
DONE = "successful,failed"
PAGE = 50


def seconds(text):
    """AAP's '2026-10-21T06:01:12.512345Z' -> seconds since 1970, or None."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)", str(text or ""))
    if not m:
        return None
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def history(api, job_id, template_id, days, max_runs, skip, now=None):
    """(runs oldest first, counts, why): why is '' when AAP answered."""
    why = api.probe()
    counts = {"listed": 0, "read": 0, "skipped": 0, "no_point": 0}
    if why:
        return [], counts, why
    since = (now or time.time()) - days * 86400.0 - 2 * 3600.0     # with room: the report takes the window's runs exactly
    out, page, more = [], 1, True
    while more and counts["listed"] < max_runs:
        code, data = api.call("GET", "jobs/?job_template=%d&id__lt=%d&status__in=%s&order_by=-id&page_size=%d&page=%d"
                              % (template_id, job_id, DONE, PAGE, page))
        if code == 404 and page > 1:                      # past the last page
            break
        if code != 200:
            return [], counts, (
                "AAP refused the list of this template's jobs (HTTP %d): %s - its user needs Organization Auditor on the "
                "template's organization" % (code, error_text(data)) if code in (401, 403) else
                "AAP answered HTTP %d for this template's jobs: %s" % (code, error_text(data)))
        results = data.get("results") or []
        more = bool(data.get("next")) and bool(results)
        page += 1
        for j in results:
            if not isinstance(j, dict) or not isinstance(j.get("id"), int):
                continue
            done = seconds(j.get("finished"))
            if done is not None and done < since:          # newest first: everything after this is older still
                more = False
                break
            if counts["listed"] >= max_runs:
                more = False
                break
            counts["listed"] += 1
            if j["id"] in skip:
                counts["skipped"] += 1
                continue
            c, d = api.call("GET", "jobs/%d/" % j["id"])      # the list leaves the artifacts out
            if c == -1:                                       # cut off on its way (large artifacts): once more
                c, d = api.call("GET", "jobs/%d/" % j["id"])
            art = d.get("artifacts") if c == 200 and isinstance(d, dict) else None
            point = art.get("ontap_capacity") if isinstance(art, dict) else None
            if isinstance(point, dict) and point.get("read_at") and isinstance(point.get("clusters"), list):
                out.append(dict(point, job=j["id"]))
                counts["read"] += 1
            else:
                counts["no_point"] += 1
    if not counts["listed"]:
        why = hidden(api, job_id, template_id)
        if why:
            return [], counts, why
    return list(reversed(out)), counts, ""


def hidden(api, job_id, template_id):
    """'' when this job (and so its template's jobs) is visible to the credential's user - the list was empty because
    this is the template's first run - else why not: AAP leaves the jobs a user may not see out of a list, without an
    error."""
    code, data = api.call("GET", "jobs/%d/" % job_id)
    if code == 200:
        return ""
    if code in (403, 404):
        return ("%s cannot see this template's jobs - AAP listed none, and not even this job %d: give %s Organization Auditor "
                "on the organization of job template %d (or a role on the template), or attach the credential of the AAP "
                "this job runs on" % ("The AAP user " + api.user if api.user else "The credential's AAP user", job_id,
                                      api.user or "that user", template_id))
    return "AAP answered HTTP %d for this job %d: %s" % (code, job_id, error_text(data))


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), template_id=dict(type="int", required=True),
                                              days=dict(type="float", default=30), max_runs=dict(type="int", default=400),
                                              skip_jobs=dict(type="list", elements="int", default=[]),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    counts = {}
    try:
        runs, counts, why = history(api, p["job_id"], p["template_id"], p["days"], max(1, p["max_runs"]), set(p["skip_jobs"] or []))
    except Exception as e:                                # noqa: BLE001 - never fail the job: the report goes out with what it has
        runs, why = [], "reading the earlier runs from AAP failed: %s: %s" % (e.__class__.__name__, e)
    module.exit_json(changed=False, ok=not why, why=why, runs=runs, jobs=counts,
                     msg=why or "%d earlier point(s) from AAP's job history (%d job(s) listed)" % (len(runs), counts.get("listed", 0)))


if __name__ == "__main__":
    main()
