#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The report jobs whose pages the job "Report website - copy" (playbooks/report_web_copy.yml) writes on the report
website: the jobs that kept their report for it (report_web_copy: true - the job artifact site_report_page), from AAP's
own API. Every job finished since the copy job's last run, or the jobs named. How far a run got is its own job artifact
site_report_copied (until, seen), which the next run starts from; a dry run (Check) or a run of named jobs keeps none,
so it never moves that on. The address and login come from the "Red Hat Ansible Automation Platform" credential's
variables (plugins/module_utils/site_aap.py). Read only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_report_copy
short_description: The report jobs that kept their report for the report website's copy job, from AAP
description:
  - Finds where the last copy got to - the newest earlier run of this job template (job type Run) that kept the job
    artifact site_report_copied - and lists the jobs finished since then (a few minutes before, the ones it already saw
    left out), oldest first. Without such a run, the jobs of the last hours.
  - Reads each job's artifacts for site_report_page (each report it kept), and checks that each can be read; a job
    whose report cannot be read is a warning, never a failure.
  - At most max_pages reports a run; the rest are the next run's (more is true, and until is where this run stopped).
  - jobs - these job IDs only, whatever the last copy got to.
  - Read only, also in check mode. The AAP user needs Organization Auditor on the organizations of the report
    templates (it only sees their jobs then).
options:
  job_id: {description: This job's ID (awx_job_id; 0 outside AAP)., type: int, default: 0}
  template_id: {description: Its job template's ID (awx_job_template_id; 0 outside AAP)., type: int, default: 0}
  jobs: {description: These job IDs only (report_web_copy_jobs)., type: list, elements: str, default: []}
  hours: {description: Without an earlier copy - the jobs of the last hours (report_web_copy_hours)., type: int, default: 24}
  max_pages: {description: Reports a run copies at most (report_web_copy_max_pages)., type: int, default: 50}
  overlap_minutes: {description: The jobs finished this many minutes before the last copy's end are listed again (those it saw left out)., type: int, default: 5}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
reports: {description: "The reports to copy, oldest first: {job, template, finished, status, packed}.", type: list}
since: {description: "Where the list starts (UTC, AAP's clock) - '' with jobs.", type: str}
until: {description: "Where the next run starts: the last job looked at (UTC) - '' with jobs.", type: str}
seen: {description: The jobs looked at that finished near until (the next run leaves them out)., type: list}
more: {description: There were more reports than max_pages - the next run copies them., type: bool}
looked: {description: Jobs looked at., type: int}
warnings_text: {description: "Jobs whose report cannot be read, and why.", type: list}
problems: {description: Why AAP could not be read (the job stops)., type: list}
'''

import datetime

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text
from ansible.module_utils.site_report_page import unpack

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Organization Auditor on the organizations of the report templates)')
DONE = "successful,failed,error,canceled"


def parse_time(text):
    """AAP's time (2026-10-24T06:01:12.512345Z) as a UTC datetime, or None."""
    t = str(text or "").strip().replace("+00:00", "Z")
    if not t.endswith("Z"):
        return None
    t = t[:-1]
    frac = 0
    if "." in t:
        t, f = t.split(".", 1)
        frac = int((f + "000000")[:6]) if f.isdigit() else 0
    try:
        return datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M:%S").replace(microsecond=frac)
    except ValueError:
        return None


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _path_after(api, nxt):
    return nxt.split(api.base, 1)[1] if api.base in nxt else nxt.lstrip("/")


def last_copy(api, template_id, job_id):
    """Where the newest earlier run (job type Run) of this template that kept site_report_copied got to: (until, seen) or
    ('', []). A dry run and a run of named jobs keep none - they are passed over."""
    if not template_id:
        return "", []
    code, data = api.call("GET", "jobs/?job_template=%d&id__lt=%d&job_type=run&order_by=-id&page_size=100"
                          % (template_id, job_id or 2 ** 31 - 1))
    if code != 200:
        raise IOError("AAP answered HTTP %d for this template's earlier runs: %s" % (code, error_text(data)))
    for j in data.get("results") or []:
        if not isinstance(j, dict) or not isinstance(j.get("id"), int):
            continue
        c, d = api.call("GET", "jobs/%d/" % j["id"])      # the list leaves the artifacts out
        rec = (d.get("artifacts") or {}).get("site_report_copied") if c == 200 and isinstance(d.get("artifacts"), dict) else None
        if isinstance(rec, dict) and parse_time(rec.get("until")):
            return str(rec["until"]), [int(x) for x in rec.get("seen") or [] if str(x).isdigit()]
    return "", []


def kept(api, j, out, warnings):
    """The reports job j kept (its artifact site_report_page), each checked: appended to out."""
    c, d = api.call("GET", "jobs/%d/" % j["id"])
    if c != 200:
        warnings.append("job %s: AAP answered HTTP %d for it: %s" % (j["id"], c, error_text(d)))
        return
    art = d.get("artifacts") if isinstance(d.get("artifacts"), dict) else {}
    packed = art.get("site_report_page")
    if packed is None:
        return
    for text in packed if isinstance(packed, list) else [packed]:
        name = str(d.get("name") or (d.get("summary_fields") or {}).get("job_template", {}).get("name") or "")
        try:
            doc = unpack(text)
        except ValueError as e:
            warnings.append("job %s (%s): its report cannot be read - %s" % (j["id"], name, e))
            continue
        out.append({"job": j["id"], "template": str(doc["data"].get("template") or name), "finished": str(d.get("finished") or ""),
                    "status": str(d.get("status") or ""), "when": str(doc["data"].get("when") or ""), "packed": text,
                    "url": str(doc.get("url") or ""), "page": str(doc["files"].get("page") or "")})


def find(api, p, now):
    """The module's answer (a dict)."""
    out, warnings = [], []
    if p["jobs"]:
        ids = []
        for x in p["jobs"]:
            if not str(x).strip().isdigit():
                return {"problems": ["report_web_copy_jobs: %s is not a job ID" % x]}
            ids.append(int(str(x).strip()))
        for i in sorted(set(ids)):
            before = len(out)
            kept(api, {"id": i}, out, warnings)
            if len(out) == before and not any(w.startswith("job %d" % i) for w in warnings):
                warnings.append("job %d: kept no report for the copy job (report_web_copy is off for its template, it ran "
                                "with Check, or it sent no report)" % i)
        return {"reports": out, "warnings_text": warnings, "since": "", "until": "", "seen": [], "more": False, "looked": len(ids)}
    until, seen = last_copy(api, p["template_id"], p["job_id"])
    if until:
        start = parse_time(until) - datetime.timedelta(minutes=max(0, p["overlap_minutes"]))
    else:
        start = now - datetime.timedelta(hours=max(1, p["hours"]))
    seen = set(seen)
    path = "jobs/?finished__gt=%s&status__in=%s&job_type=run&order_by=finished,id&page_size=200" % (iso(start), DONE)
    looked, last, looked_at, more = 0, None, [], False
    for _ in range(200):
        code, data = api.call("GET", path)
        if code != 200:
            raise IOError("AAP refused the list of jobs (HTTP %d): %s%s" % (code, error_text(data), (
                " - the copy job's AAP user needs Organization Auditor on the organizations of the report templates"
                if code in (401, 403) else "")))
        for j in data.get("results") or []:
            if not isinstance(j, dict) or not isinstance(j.get("id"), int):
                continue
            if len(out) >= max(1, p["max_pages"]):
                more = True
                break
            fin = parse_time(j.get("finished"))
            looked_at.append((j["id"], fin))
            if fin is not None and (last is None or fin > last):
                last = fin
            if j["id"] in seen or (p["template_id"] and j.get("job_template") == p["template_id"]):
                continue
            looked += 1
            kept(api, j, out, warnings)
        if more or not data.get("next"):
            break
        path = _path_after(api, data["next"])
    if last is None:                                     # nothing finished since: the next run starts at the same place
        last = parse_time(until) if until else start
    edge = last - datetime.timedelta(minutes=max(0, p["overlap_minutes"]))
    # nothing listed: the same place as before, so the same jobs seen
    near = sorted(i for i, fin in looked_at if fin is not None and fin >= edge) if looked_at else sorted(seen)
    out.sort(key=lambda r: (r["when"], r["job"]))
    return {"reports": out, "warnings_text": warnings, "since": iso(start), "until": iso(last), "seen": near, "more": more,
            "looked": looked}


def main():
    module = AnsibleModule(argument_spec=dict(
        job_id=dict(type="int", default=0), template_id=dict(type="int", default=0), jobs=dict(type="list", elements="str", default=[]),
        hours=dict(type="int", default=24), max_pages=dict(type="int", default=50), overlap_minutes=dict(type="int", default=5),
        ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)), supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, problems=[why], reports=[], warnings_text=[], since="", until="", seen=[], more=False, looked=0)
    try:
        r = find(api, p, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
    except IOError as e:
        module.exit_json(changed=False, problems=[str(e)], reports=[], warnings_text=[], since="", until="", seen=[], more=False, looked=0)
    r.setdefault("problems", [])
    for k, v in (("reports", []), ("warnings_text", []), ("since", ""), ("until", ""), ("seen", []), ("more", False), ("looked", 0)):
        r.setdefault(k, v)
    module.exit_json(changed=False, **r)


if __name__ == "__main__":
    main()
