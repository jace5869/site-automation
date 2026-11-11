#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The report jobs whose pages the job "Report website - copy" (playbooks/report_web_copy.yml) writes on the report
website: the jobs that kept their report for it (report_web_copy: true - the job artifact site_report_page), from AAP's
own API. Every job finished since the copy job's last run, or the jobs named. How far a run got is its own job artifact
site_report_copied (until, seen, and the jobs whose page failed - failed), which the next run starts from, those jobs
tried again first; a dry run (Check) or a run of named jobs keeps none, so it never moves that on. The address and
login come from the "Red Hat Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py).
Read only. Standard library only."""
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
  - A kept report whose file names (page, latest, index) are not names inside report_web_dest - a path below the folder
    with / between its parts, ending in .html - is not copied, and a warning names the job and the name.
  - The jobs the last copy could not write a page for (its site_report_copied failed) are tried again first, each
    named in a warning, every run until their page is written (the copy job keeps them in failed again when it fails).
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
retry: {description: "The jobs of the last copy's failed that AAP did not answer for now: [{job, why}] - kept for the next run.", type: list}
retrying: {description: "The jobs the last copy could not write a page for, tried again first - one line each (also a warning).", type: list}
more: {description: There were more reports than max_pages - the next run copies them., type: bool}
looked: {description: Jobs looked at., type: int}
warnings_text: {description: "Jobs whose report cannot be read, and why.", type: list}
problems: {description: Why AAP could not be read (the job stops)., type: list}
'''

import datetime
import re

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


def safe_name(rel):
    """A file name inside report_web_dest - the same rule as site_report_web's safe_name (and report_web_files): a
    relative path with / between its parts, no leading / or \\, no drive or ':', no backslash, no empty, '.' or '..'
    part, ending in .html."""
    return (isinstance(rel, str) and rel.endswith(".html") and "\\" not in rel and ":" not in rel
            and not any(x in ("", ".", "..") for x in rel.split("/")))


def _failed(rec):
    """The jobs a copy could not write a page for, from its site_report_copied: [(job, why)], each job once."""
    out, ids = [], set()
    for x in (rec.get("failed") if isinstance(rec.get("failed"), list) else []):
        i, why = (x.get("job"), x.get("why")) if isinstance(x, dict) else (x, "")
        if str(i).strip().isdigit() and int(str(i).strip()) not in ids:
            ids.add(int(str(i).strip()))
            out.append((int(str(i).strip()), re.sub(r"\s+", " ", str(why or "it failed")).strip()[:300]))
    return out


def last_copy(api, template_id, job_id):
    """Where the newest earlier run (job type Run) of this template that kept site_report_copied got to: (until, seen,
    failed) or ('', [], []). A dry run and a run of named jobs keep none - they are passed over."""
    if not template_id:
        return "", [], []
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
            return str(rec["until"]), [int(x) for x in rec.get("seen") or [] if str(x).isdigit()], _failed(rec)
    return "", [], []


def kept(api, j, out, warnings):
    """The reports job j kept (its artifact site_report_page), each checked: appended to out. False when the job could
    not be read (AAP did not answer 200 for it): the next run must look at it again; None when AAP has it no more (404)."""
    c, d = api.call("GET", "jobs/%d/" % j["id"])
    if c == 404:
        warnings.append("job %s: AAP answered HTTP 404 for it (deleted, or its user may not see it) - not looked at again" % j["id"])
        return None
    if c != 200:
        warnings.append("job %s: AAP answered HTTP %d for it: %s - the next run looks at it again" % (j["id"], c, error_text(d)))
        return False
    art = d.get("artifacts") if isinstance(d.get("artifacts"), dict) else {}
    packed = art.get("site_report_page")
    if packed is None:
        return True
    for text in packed if isinstance(packed, list) else [packed]:
        name = str(d.get("name") or (d.get("summary_fields") or {}).get("job_template", {}).get("name") or "")
        try:
            doc = unpack(text)
        except ValueError as e:
            warnings.append("job %s (%s): its report cannot be read - %s" % (j["id"], name, e))
            continue
        files = doc["files"]
        bad = [str(files.get(k)) for k in ("page", "latest", "index") if files.get(k) and not safe_name(files.get(k))]
        if bad:                                          # site_report_web refuses them too: never written outside the folder
            warnings.append("job %s (%s): its page %s is not copied - %s: not a file name inside report_web_dest (a path below "
                            "the folder with / between its parts, ending in .html)" % (j["id"], name, files.get("page"), ", ".join(bad)))
            continue
        out.append({"job": j["id"], "template": str(doc["data"].get("template") or name), "finished": str(d.get("finished") or ""),
                    "status": str(d.get("status") or ""), "when": str(doc["data"].get("when") or ""), "packed": text,
                    "url": str(doc.get("url") or ""), "page": str(doc["files"].get("page") or "")})
    return True


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
    until, seen, failed = last_copy(api, p["template_id"], p["job_id"])
    # the jobs the last copy could not write a page for: first, named every run until their page is written
    retrying, retry, done = [], [], set()
    for i, why in failed:
        retrying.append("job %d: its page was not copied by the last copy (%s) - tried again now" % (i, why))
        if kept(api, {"id": i}, out, warnings) is False:
            retry.append({"job": i, "why": why})        # AAP did not answer for it: the next run, again
        done.add(i)
    if until:
        start = parse_time(until) - datetime.timedelta(minutes=max(0, p["overlap_minutes"]))
    else:
        start = now - datetime.timedelta(hours=max(1, p["hours"]))
    seen = set(seen)
    path = "jobs/?finished__gt=%s&status__in=%s&job_type=run&order_by=finished,id&page_size=200" % (iso(start), DONE)
    looked, last, looked_at, more, unread = 0, None, [], False, []
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
            if j["id"] in seen or j["id"] in done or (p["template_id"] and j.get("job_template") == p["template_id"]):
                continue
            looked += 1
            if kept(api, j, out, warnings) is False:
                unread.append((j["id"], fin))
        if more or not data.get("next"):
            break
        path = _path_after(api, data["next"])
    if last is None:                                     # nothing finished since: the next run starts at the same place
        last = parse_time(until) if until else start
    # a job AAP did not answer for is not passed over: the next run starts no later than it, and it is not "seen"
    bad = set(i for i, _ in unread)
    fins = [fin for _, fin in unread if fin is not None]
    if fins and last > min(fins):
        last = min(fins)
    edge = last - datetime.timedelta(minutes=max(0, p["overlap_minutes"]))
    # nothing listed: the same place as before, so the same jobs seen
    near = sorted(i for i, fin in looked_at if fin is not None and fin >= edge and i not in bad) if looked_at else sorted(seen)
    out.sort(key=lambda r: (r["when"], r["job"]))
    return {"reports": out, "warnings_text": warnings, "since": iso(start), "until": iso(last), "seen": near, "more": more,
            "looked": looked, "retry": retry, "retrying": retrying}


def main():
    module = AnsibleModule(argument_spec=dict(
        job_id=dict(type="int", default=0), template_id=dict(type="int", default=0), jobs=dict(type="list", elements="str", default=[]),
        hours=dict(type="int", default=24), max_pages=dict(type="int", default=50), overlap_minutes=dict(type="int", default=5),
        ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)), supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="report_history_aap_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, problems=[why], reports=[], warnings_text=[], since="", until="", seen=[], more=False, looked=0,
                         retry=[])
    try:
        r = find(api, p, datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None))
    except IOError as e:
        module.exit_json(changed=False, problems=[str(e)], reports=[], warnings_text=[], since="", until="", seen=[], more=False, looked=0,
                         retry=[])
    r.setdefault("problems", [])
    for k, v in (("reports", []), ("warnings_text", []), ("since", ""), ("until", ""), ("seen", []), ("more", False), ("looked", 0),
                 ("retry", [])):
        r.setdefault(k, v)
    r.setdefault("retrying", [])
    for w in r["retrying"]:
        module.warn("The report website copy: %s" % w)
    module.exit_json(changed=False, **r)


if __name__ == "__main__":
    main()
