#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP backup Phase 1 (1.142.0, roles/aap_backup):
Optional AAP controller API helper (runs on localhost with optional Red Hat Ansible Automation Platform credential):
  - pre_check: read-only snapshot of active/pending AAP jobs before backup (skipped cleanly when no AAP credential is attached).
  - schedule:  reconcile the optional weekly Europe/Berlin backup schedule on "AAP - backup" (off by default).
Standard library + ansible.module_utils.site_aap only; Python 3.6+ compatible.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_backup_schedule
short_description: Optional AAP API pre-check and weekly Berlin schedule helper for AAP - backup
description:
  - pre_check records active/pending AAP jobs (excluding self_job_id) when an AAP credential is attached; skips cleanly otherwise.
  - schedule reconciles a weekly schedule (default Europe/Berlin, off by default) on the backup job template via AAP API.
options:
  action:
    description: Operation (pre_check or schedule).
    type: str
    required: true
    choices: [pre_check, schedule]
  enabled:
    description: Whether the weekly schedule is enabled (when false in schedule action, does nothing).
    type: bool
    default: false
  schedule_name:
    description: Name of the schedule in AAP.
    type: str
    default: Weekly AAP backup
  schedule_when:
    description: Weekly specification in 24-hour format ("weekly Sun 02:00").
    type: str
    default: weekly Sun 02:00
  schedule_timezone:
    description: IANA time zone (default Europe/Berlin).
    type: str
    default: Europe/Berlin
  template_name:
    description: Name of the AAP job template to attach the schedule to.
    type: str
    default: AAP - backup
  organization:
    description: AAP organization name containing template_name.
    type: str
    default: Default
  self_job_id:
    description: Current AAP job ID (awx_job_id) excluded from pre_check active jobs list.
    type: str
    default: ''
  ca_path:
    description: Optional CA bundle path for AAP HTTPS verification.
    type: str
    default: ''
  timeout:
    description: Timeout in seconds per AAP API request.
    type: int
    default: 30
author: site-automation (1.142.0)
'''

import datetime
import os
import re
import sys
import tempfile
import traceback
import uuid
from urllib.parse import quote

try:
    from ansible.module_utils.basic import AnsibleModule
    from ansible.module_utils import site_aap
except ImportError:
    AnsibleModule = None
    site_aap = None

DAYS = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}
TZ_RE = re.compile(r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*$")

_SECRET_KV_RE = re.compile(
    r"(?i)(\b[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|vault[_-]?pass|private[_-]?key)[A-Za-z0-9_.-]*\s*[=:]\s*)(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)
_SECRET_AUTH_RE = re.compile(r"(?i)(\b(?:Bearer|Basic|Token)\s+)([^\s,;\"']+)")
_SECRET_URL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^/\s@]+)(@)")


def _redact_secrets(text):
    s = str(text or "")
    if not s:
        return ""
    s = _SECRET_URL_RE.sub(r"\1<redacted>\3", s)
    s = _SECRET_AUTH_RE.sub(r"\1<redacted>", s)
    s = _SECRET_KV_RE.sub(r"\1<redacted>", s)
    return s


def _format_exception(exc):
    tb = getattr(exc, "__traceback__", None)
    if tb is None:
        tb = sys.exc_info()[2]
    loc = ""
    if tb is not None:
        try:
            frames = traceback.extract_tb(tb)
            if frames:
                last = frames[-1]
                loc = " (%s:%d)" % (os.path.basename(last.filename), last.lineno)
        except Exception:
            loc = ""
    msg_lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    last_line = _redact_secrets(msg_lines[-1]) if msg_lines else ""
    head = "%s: %s" % (type(exc).__name__, last_line) if last_line else type(exc).__name__
    return "%s%s" % (head, loc)


def _schedule_stop(summary, cause, extra_text=""):
    code = "SA-AAPBK-16"
    anchor = code.lower()
    one_line = " ".join(str(summary or "").splitlines()).strip().rstrip(".")
    headline = "%s: %s. See docs/AAP_BACKUP.md#%s" % (code, one_line, anchor)
    clean_cause = " ".join(_redact_secrets(cause).splitlines()).strip()
    now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        log_path = os.path.abspath(
            os.path.join(
                tempfile.gettempdir(),
                "sa-aap-backup-schedule-%s-%d-%s.log" % (now_str, os.getpid(), uuid.uuid4().hex[:6]),
            )
        )
        lines = [
            "time=%s" % now_str,
            "code=%s" % code,
            "summary=%s" % headline,
            "cause=%s" % clean_cause,
        ]
        if extra_text:
            lines.append("--- details ---")
            lines.append(_redact_secrets(extra_text))
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.chmod(log_path, 0o600)
    except Exception as exc:
        log_path = "AAP job output (log file unavailable: %s)" % type(exc).__name__
    msg = "%s\nCause: %s | Log: %s" % (headline, clean_cause, log_path)
    return {
        "failed": True,
        "stop_code": code,
        "cause": clean_cause,
        "log_path": log_path,
        "doc_ref": "docs/AAP_BACKUP.md#%s" % anchor,
        "msg": msg,
    }


def build_weekly_rrule(when, tz="Europe/Berlin"):
    """Build AAP RRULE for 'weekly <Mon..Sun> HH:MM' in IANA time zone tz."""
    tz_clean = str(tz or "Europe/Berlin").strip()
    if not tz_clean or not TZ_RE.match(tz_clean):
        raise ValueError("invalid time zone %r (use an IANA zone such as Europe/Berlin)" % tz)
    parts = str(when or "").strip().lower().split()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", parts[-1]) if parts else None
    if not m or len(parts) != 3 or parts[0] != "weekly" or parts[1][:3] not in DAYS:
        raise ValueError("%r is not 'weekly <Mon..Sun> HH:MM' (for example 'weekly Sun 02:00')" % when)
    hh, mm = int(m.group(1)), m.group(2)
    return "DTSTART;TZID=%s:20260105T%02d%s00 RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=%s" % (
        tz_clean, hh, mm, DAYS[parts[1][:3]]
    )


def _api_all(api, path):
    rows = []
    next_path = path
    for _ in range(50):
        code, data = api.call("GET", next_path)
        if code != 200:
            raise RuntimeError("AAP GET %s returned HTTP %d: %s" % (next_path, code, site_aap.error_text(data)))
        rows.extend(data.get("results") or [])
        nxt = data.get("next")
        if not nxt:
            break
        if api.base and ("/" + api.base) in nxt:
            next_path = nxt.split("/" + api.base, 1)[1]
        elif nxt.startswith("/"):
            next_path = nxt.lstrip("/")
        else:
            break
    return rows


def run_pre_check(api, self_job_id=""):
    err = api.probe()
    if err:
        return {"changed": False, "skipped": True, "reason": err, "running_jobs": []}
    rows = _api_all(api, "jobs/?status__in=pending,waiting,running&page_size=100")
    self_id = str(self_job_id or "").strip()
    jobs = []
    for r in rows:
        jid = str(r.get("id") or "")
        if self_id and jid == self_id:
            continue
        jobs.append({
            "id": r.get("id"),
            "name": str(r.get("name") or ""),
            "status": str(r.get("status") or ""),
            "started": str(r.get("started") or ""),
        })
    return {
        "changed": False,
        "skipped": False,
        "running_jobs": jobs,
        "msg": "Recorded %d other active/pending AAP job(s) before backup." % len(jobs),
    }


def reconcile_schedule(api, params, check_mode=False):
    if not params.get("enabled", False):
        return {
            "changed": False,
            "enabled": False,
            "action_taken": "disabled",
            "msg": "Weekly backup schedule is off (aap_backup_schedule_enabled: false); AAP schedules untouched.",
        }
    try:
        rrule = build_weekly_rrule(params.get("schedule_when", "weekly Sun 02:00"), params.get("schedule_timezone", "Europe/Berlin"))
    except ValueError as exc:
        return _schedule_stop(
            "Invalid weekly backup schedule - set aap_backup_schedule_when ('weekly Sun 02:00') and a valid IANA timezone",
            _format_exception(exc),
            extra_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    err = api.probe()
    if err:
        return _schedule_stop(
            "Cannot connect to AAP API for weekly schedule - attach an AAP credential and verify controller reachability",
            "AAP API probe failed: %s" % err,
        )

    org_name = str(params.get("organization") or "Default").strip()
    tpl_name = str(params.get("template_name") or "AAP - backup").strip()
    sched_name = str(params.get("schedule_name") or "Weekly AAP backup").strip()

    try:
        orgs = [o for o in _api_all(api, "organizations/?name=%s" % quote(org_name, safe="")) if o.get("name") == org_name]
        if len(orgs) != 1:
            return _schedule_stop(
                "AAP organization not found uniquely - set aap_backup_schedule_organization to an existing organization",
                "GET organizations/?name=%s returned %d exact match(es) for %r" % (quote(org_name, safe=""), len(orgs), org_name),
            )
        org_id = int(orgs[0]["id"])
        tpls = [
            t for t in _api_all(api, "job_templates/?name=%s" % quote(tpl_name, safe=""))
            if t.get("name") == tpl_name and int(t.get("organization") or 0) == org_id
        ]
        if len(tpls) != 1:
            return _schedule_stop(
                "AAP backup job template not found uniquely - set aap_backup_schedule_template or run AAP - set up job templates",
                "GET job_templates/?name=%s in organization %r returned %d exact match(es)" % (
                    quote(tpl_name, safe=""), org_name, len(tpls)
                ),
            )
        tid = int(tpls[0]["id"])
        existing = [s for s in _api_all(api, "job_templates/%d/schedules/" % tid) if s.get("name") == sched_name]
    except Exception as exc:
        return _schedule_stop(
            "AAP API query failed while reconciling weekly schedule - check AAP credential permissions and controller health",
            _format_exception(exc),
            extra_text="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

    want = {
        "name": sched_name,
        "rrule": rrule,
        "enabled": True,
        "description": "Managed by site-automation 1.142.0 (aap_backup_schedule_enabled: true)",
    }
    if not existing:
        if not check_mode:
            code, data = api.call("POST", "job_templates/%d/schedules/" % tid, want)
            if code not in (200, 201):
                return _schedule_stop(
                    "Failed to create weekly backup schedule in AAP - verify AAP Admin permission on the organization",
                    "POST job_templates/%d/schedules/ for %r returned HTTP %d: %s" % (
                        tid, sched_name, code, site_aap.error_text(data)
                    ),
                )
        return {
            "changed": True,
            "enabled": True,
            "action_taken": "would_create" if check_mode else "created",
            "rrule": rrule,
            "template_name": tpl_name,
            "schedule_name": sched_name,
            "msg": "%s weekly schedule %r on %r (%s)." % ("Would create" if check_mode else "Created", sched_name, tpl_name, rrule),
        }

    cur = existing[0]
    diff = {}
    for k in ("rrule", "enabled"):
        if cur.get(k) != want[k]:
            diff[k] = [cur.get(k), want[k]]
    if not diff:
        return {
            "changed": False,
            "enabled": True,
            "action_taken": "unchanged",
            "rrule": rrule,
            "template_name": tpl_name,
            "schedule_name": sched_name,
            "msg": "Weekly schedule %r on %r is already up to date (%s)." % (sched_name, tpl_name, rrule),
        }
    if not check_mode:
        code, data = api.call("PATCH", "schedules/%d/" % int(cur["id"]), want)
        if code not in (200, 201):
            return _schedule_stop(
                "Failed to update weekly backup schedule in AAP - verify AAP Admin permission on the organization",
                "PATCH schedules/%d/ for %r returned HTTP %d: %s" % (
                    int(cur["id"]), sched_name, code, site_aap.error_text(data)
                ),
            )
    return {
        "changed": True,
        "enabled": True,
        "action_taken": "would_update" if check_mode else "updated",
        "diff": diff,
        "rrule": rrule,
        "template_name": tpl_name,
        "schedule_name": sched_name,
        "msg": "%s weekly schedule %r on %r (%s)." % ("Would update" if check_mode else "Updated", sched_name, tpl_name, rrule),
    }


def main():
    module = AnsibleModule(
        argument_spec=dict(
            action=dict(type="str", required=True, choices=["pre_check", "schedule"]),
            enabled=dict(type="bool", default=False),
            schedule_name=dict(type="str", default="Weekly AAP backup"),
            schedule_when=dict(type="str", default="weekly Sun 02:00"),
            schedule_timezone=dict(type="str", default="Europe/Berlin"),
            template_name=dict(type="str", default="AAP - backup"),
            organization=dict(type="str", default="Default"),
            self_job_id=dict(type="str", default=""),
            ca_path=dict(type="str", default=""),
            timeout=dict(type="int", default=30),
        ),
        supports_check_mode=True,
    )
    conn = site_aap.connection()
    api = site_aap.Api(
        conn,
        ca_path=module.params["ca_path"],
        timeout=module.params["timeout"],
        ca_setting="aap_backup_ca_path",
    )
    if module.params["action"] == "pre_check":
        res = run_pre_check(api, self_job_id=module.params["self_job_id"])
    else:
        res = reconcile_schedule(api, module.params, check_mode=module.check_mode)
    if res.get("failed"):
        module.fail_json(**res)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
