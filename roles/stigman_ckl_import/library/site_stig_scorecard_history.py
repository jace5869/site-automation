#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The STIG compliance scorecard's earlier points, from AAP's own job history (1.58.0; playbooks/stigman_ckl_import.yml
with stigman_ckl_import_mode "import and scorecard" or "scorecard"). Every run with the scorecard keeps what it read as
the job artifact stig_scorecard (one point: each collection's open findings by category, and per rule on how many hosts
it is open). This reads the artifacts of the same job template's earlier jobs back - as far as days, and as far as AAP
keeps its jobs (the management job "Cleanup Job Details", 120 days unless changed). It also reads what the STIG drift
reports' newest jobs found fixed on the hosts and still Open in STIG Manager (their artifact stig_fixed_open). The address
and login come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py).
Read only. Never fails the job: without AAP it says why, and the scorecard shows this run's numbers alone (and the report
database's points, when one is set up). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_scorecard_history
short_description: The STIG compliance scorecard's earlier points, from its job template's earlier jobs in AAP
description:
  - Lists the finished jobs of one job template before this one, newest first, until one finished before the window
    (days), then reads each one's artifacts for stig_scorecard (the point that run kept).
  - A job in skip_jobs is not read (its point is already at hand - from the report database).
  - Reads the newest finished jobs of the job templates named in drift_templates (by name), no older than drift_days, for
    their artifact stig_fixed_open - per asset the newest job that scanned it counts.
  - Read only, also in check mode. Never fails - ok is false and why says what was missing.
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential sets it
    (CONTROLLER_HOST, CONTROLLER_OAUTH_TOKEN or CONTROLLER_USERNAME and CONTROLLER_PASSWORD, CONTROLLER_VERIFY_SSL, or
    the AAP_* / TOWER_* names). Its user must be able to see the templates' jobs - Organization Auditor on the templates'
    organization (or any role on the templates).
options:
  job_id: {description: This job's ID (awx_job_id) - only runs before it are read., type: int, required: true}
  template_id: {description: Its job template's ID (awx_job_template_id)., type: int, required: true}
  days: {description: How far back., type: float, default: 64}
  max_runs: {description: Read at most this many jobs., type: int, default: 200}
  skip_jobs: {description: Job IDs whose point is already at hand., type: list, elements: int, default: []}
  drift_templates: {description: "The STIG drift reports' job template names (stigman_scorecard_drift_templates; [] = none read).", type: list, elements: str, default: []}
  drift_days: {description: Their jobs no older than this many days (stigman_scorecard_drift_days)., type: float, default: 14}
  drift_jobs: {description: Read at most this many jobs per drift template., type: int, default: 20}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: AAP answered., type: bool}
why: {description: Why there are no earlier points, when ok is false., type: str}
runs:
  description: The earlier runs' points, oldest first - each the job's stig_scorecard artifact with the job's ID (job).
  type: list
  sample: [{job: 4180, v: 1, read_at: "2026-10-21T06:00:05Z", collections: [{id: "1", name: SITEA - RHEL 9, assets: 8, cat1: 1, cat2: 20, cat3: 2}]}]
jobs: {description: "How many jobs were listed, read, skipped (skip_jobs) and had no point (the import alone, or failed before it).", type: dict}
fixed:
  description: "What the drift reports found fixed on the host and still Open in STIG Manager: {jobs: [{job, template, read_at, assets}],
                rows: [[host, collection, asset, SV-number, category, title, when scanned]], why: [a line per template not read]}"
  type: dict
'''

import calendar
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import quote
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


def artifacts(api, job):
    """A job's artifacts ({} when it has none or cannot be read); the jobs list leaves them out."""
    c, d = api.call("GET", "jobs/%d/" % job)
    if c == -1:                                           # cut off on its way (large artifacts): once more
        c, d = api.call("GET", "jobs/%d/" % job)
    art = d.get("artifacts") if c == 200 and isinstance(d, dict) else None
    return art if isinstance(art, dict) else {}


def history(api, job_id, template_id, days, max_runs, skip, now=None):
    """(runs oldest first, counts, why): why is '' when AAP answered."""
    why = api.probe()
    counts = {"listed": 0, "read": 0, "skipped": 0, "no_point": 0}
    if why:
        return [], counts, why
    since = (now or time.time()) - days * 86400.0 - 2 * 3600.0
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
            point = artifacts(api, j["id"]).get("stig_scorecard")
            if isinstance(point, dict) and point.get("read_at") and isinstance(point.get("collections"), list):
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


def fixed(api, templates, days, max_jobs, now=None):
    """The drift reports' stig_fixed_open artifacts -> {jobs, rows, why}. Newest job first, and an asset counts once: the
    newest job that scanned it (its rows, or none - it is in that job's assets) wins over an older job's."""
    out = {"jobs": [], "rows": [], "why": []}
    since = (now or time.time()) - days * 86400.0
    seen = set()
    found = []
    for name in templates:
        code, data = api.call("GET", "jobs/?job_template__name=%s&status__in=%s&order_by=-id&page_size=%d"
                              % (quote(name, safe=""), DONE, max_jobs))
        if code != 200:
            out["why"].append("%s: AAP answered HTTP %d for its jobs: %s" % (name, code, error_text(data)))
            continue
        results = [j for j in data.get("results") or [] if isinstance(j, dict) and isinstance(j.get("id"), int)]
        got = 0
        for j in results:
            done = seconds(j.get("finished"))
            if done is not None and done < since:
                break
            art = artifacts(api, j["id"]).get("stig_fixed_open")
            if isinstance(art, dict) and isinstance(art.get("rows"), list):
                found.append((j["id"], name, art))
                got += 1
        if not got:
            out["why"].append("%s: %s" % (name, "no job of the last %g days has what it found (the artifact stig_fixed_open, kept since "
                                                "1.58.0)" % days if results else "no finished job of a template with this name"))
    for job, name, art in sorted(found, key=lambda x: -x[0]):
        assets = set(tuple(str(x) for x in a[:2]) for a in art.get("assets") or [] if isinstance(a, (list, tuple)) and len(a) >= 2)
        rows = [r for r in art["rows"] if isinstance(r, (list, tuple)) and len(r) >= 7]
        assets |= set((str(r[1]), str(r[2])) for r in rows)
        new = assets - seen
        out["rows"] += [list(r[:7]) for r in rows if (str(r[1]), str(r[2])) in new]
        seen |= assets
        out["jobs"].append({"job": job, "template": name, "read_at": str(art.get("read_at") or ""), "assets": len(assets)})
    return out


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), template_id=dict(type="int", required=True),
                                              days=dict(type="float", default=64), max_runs=dict(type="int", default=200),
                                              skip_jobs=dict(type="list", elements="int", default=[]),
                                              drift_templates=dict(type="list", elements="str", default=[]),
                                              drift_days=dict(type="float", default=14), drift_jobs=dict(type="int", default=20),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    counts, fx = {}, {"jobs": [], "rows": [], "why": []}
    try:
        runs, counts, why = history(api, p["job_id"], p["template_id"], p["days"], max(1, p["max_runs"]), set(p["skip_jobs"] or []))
    except Exception as e:                                # noqa: BLE001 - never fail the job: the report goes out with what it has
        runs, why = [], "reading the earlier runs from AAP failed: %s: %s" % (e.__class__.__name__, e)
    names = [str(n).strip() for n in p["drift_templates"] or [] if str(n).strip()]
    if not why and names:
        try:
            fx = fixed(api, names, p["drift_days"], max(1, p["drift_jobs"]))
        except Exception as e:                            # noqa: BLE001
            fx = {"jobs": [], "rows": [], "why": ["reading the drift reports' jobs from AAP failed: %s: %s" % (e.__class__.__name__, e)]}
    module.exit_json(changed=False, ok=not why, why=why, runs=runs, jobs=counts, fixed=fx,
                     msg=why or "%d earlier point(s) from AAP's job history (%d job(s) listed); %d fixed-on-host row(s) of %d drift job(s)"
                     % (len(runs), counts.get("listed", 0), len(fx["rows"]), len(fx["jobs"])))


if __name__ == "__main__":
    main()
