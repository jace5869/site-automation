#!/usr/bin/python
# -*- coding: utf-8 -*-
"""How available a service was, from this job template's own past runs in AAP: an availability job fails when the
service is down and ends well when it is up, so the share of its runs that ended well over a day, a week and a month is
the service's availability as the job saw it. Also: how the run before this one ended, and since when the service is
down. The address and login come from the "Red Hat Ansible Automation Platform" credential's variables
(plugins/module_utils/site_aap.py). Read only. Never fails the job. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_availability_history
short_description: A job template's runs that ended well and those that failed, per period, from AAP
description:
  - Counts the template's jobs that finished successful and failed in each period (canceled jobs and errors are not
    counted - they say nothing about the service), the run before this one, the last one that ended well, and the first
    failed one after it.
  - Read only, also in check mode. Never fails - ok is false and why says what was missing.
options:
  job_id: {description: This job's ID (awx_job_id)., type: int, required: true}
  template_id: {description: Its job template's ID (awx_job_template_id)., type: int, required: true}
  hours: {description: "The periods, in hours (24 = the last day).", type: list, elements: int, default: [24, 168, 720]}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 15}
author: site automation
'''

RETURN = r'''
ok: {description: AAP answered., type: bool}
why: {description: Why there is no history, when ok is false., type: str}
periods: {description: "[{hours, up (runs that ended well), down (runs that failed), pct (up / (up + down) x 100, null without runs)}]", type: list}
previous: {description: "The run before this one: {id, status, finished}, or null (the first run).", type: dict}
last_up: {description: "The last run that ended well: {id, finished}, or null.", type: dict}
down_since: {description: "The first failed run after last_up: {id, started}, or null.", type: dict}
'''

import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

NO_CREDENTIAL = ('no AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Organization Auditor on its organization) for the availability over a day, a week and a month')


def first(api, query):
    """(status, the first job of a list query or None, its count)."""
    code, data = api.call("GET", "jobs/?" + query)
    if code != 200:
        return code, data, 0
    rows = [j for j in (data.get("results") or []) if isinstance(j, dict)]
    return code, (rows[0] if rows else None), int(data.get("count") or 0)


def history(api, job_id, template_id, hours, now=None):
    why = api.probe()
    if why:
        return {"why": why}
    now = now or datetime.datetime.utcnow()
    t = "job_template=%d&id__lt=%d" % (template_id, job_id)
    code, prev, _ = first(api, t + "&status__in=successful,failed&order_by=-id&page_size=1")
    if code != 200:
        return {"why": ("AAP refused the list of this template's jobs (HTTP %d): %s - its user needs Organization Auditor on the "
                        "template's organization" % (code, error_text(prev)) if code in (401, 403) else
                        "AAP answered HTTP %d for this template's jobs: %s" % (code, error_text(prev)))}
    periods = []
    for h in hours:
        since = (now - datetime.timedelta(hours=int(h))).strftime("%Y-%m-%dT%H:%M:%SZ")
        counts = {}
        for status in ("successful", "failed"):
            c, _, n = first(api, "%s&status=%s&finished__gt=%s&page_size=1" % (t, status, since))
            counts[status] = n if c == 200 else 0
        total = counts["successful"] + counts["failed"]
        periods.append({"hours": int(h), "up": counts["successful"], "down": counts["failed"],
                        "pct": round(100.0 * counts["successful"] / total, 2) if total else None})
    _, up, _ = first(api, t + "&status=successful&order_by=-id&page_size=1")
    down = None
    if prev and prev.get("status") == "failed":
        q = t + "&status=failed&order_by=id&page_size=1" + ("&id__gt=%d" % up["id"] if up else "")
        _, down, _ = first(api, q)
    pick = lambda j, *keys: {k: j.get(k) for k in ("id",) + keys} if j else None   # noqa: E731
    return {"why": "", "periods": periods, "previous": pick(prev, "status", "finished"),
            "last_up": pick(up, "finished"), "down_since": pick(down, "started")}


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True), template_id=dict(type="int", required=True),
                                              hours=dict(type="list", elements="int", default=[24, 168, 720]),
                                              ca_path=dict(type="str", default=""), timeout=dict(type="int", default=15)),
                           supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    try:
        out = history(api, p["job_id"], p["template_id"], p["hours"])
    except Exception as e:                                # noqa: BLE001 - never fail the job: the check goes on without it
        out = {"why": "reading the past runs from AAP failed: %s: %s" % (e.__class__.__name__, e)}
    module.exit_json(changed=False, ok=not out["why"], why=out["why"], periods=out.get("periods", []),
                     previous=out.get("previous"), last_up=out.get("last_up"), down_since=out.get("down_since"),
                     msg=out["why"] or "; ".join("%dh %s" % (p["hours"], "-" if p["pct"] is None else "%s%%" % p["pct"])
                                                  for p in out.get("periods", [])))


if __name__ == "__main__":
    main()
