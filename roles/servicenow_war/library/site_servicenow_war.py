#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Everything a ServiceNow assignment group had on its plate in a week - incidents, catalog tasks, changes, change tasks,
problem tasks (any task) - read from the REST Table API for the weekly activity report (playbooks/servicenow_war.yml).
Runs on the AAP side; only reads. Each record is sorted into what the week did with it: opened in the week, completed
in the week (resolved or closed, with the date), still open at the week's end, worked on (updated) in the week."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_servicenow_war
short_description: Read what a ServiceNow assignment group opened, completed and still has open in a week
description:
  - Reads every task of the given tables (incident, sc_task, change_request, change_task, problem_task ... or task for every
    class at once) whose assignment group is one of the groups named (or whose assignee is one of the users named) and
    that the week touched - opened in it, resolved or closed in it, open at its end, or updated in it. The week is the last
    N days, the previous calendar week, this week so far, or two dates, in the time zone given.
  - Sorts each record - opened, done (with its date, resolved_at for an incident else closed_at), open at the end, touched -
    on the record's own UTC timestamps, whatever time zone the API account has. Says which tables could not be read and
    which groups or users ServiceNow does not know.
  - Only reads, in check mode too.
options:
  url: {description: The instance (https://...)., type: str, required: true}
  username: {description: The API account., type: str, required: true}
  password: {description: Its password., type: str, required: true, no_log: true}
  validate_certs: {description: Check the instance's certificate., type: bool, default: true}
  ca_path: {description: A CA bundle for the instance's certificate., type: path}
  timeout: {description: Seconds per request., type: int, default: 30}
  groups: {description: The assignment groups (names)., type: list, elements: str, default: []}
  users: {description: "Also the tasks assigned to these users (user names or full names), whatever their group.", type: list, elements: str, default: []}
  tables: {description: "The tables read (incident, sc_task, change_request, change_task, problem_task, problem, sc_req_item ...; task = every class in one read).", type: list, elements: str, default: [incident, sc_task, change_request, change_task, problem_task]}
  fields_extra: {description: More columns read from every table (your own fields)., type: list, elements: str, default: []}
  period: {description: "days = the last N days up to now (or until); last-week = the previous calendar week; this-week = this week so far; dates = since and until.", type: str, default: days}
  days: {description: The window's length for period days., type: float, default: 7}
  since: {description: "The window's start (YYYY-MM-DD or YYYY-MM-DD HH:MM, in time_zone): with it, period is dates.", type: str, default: ''}
  until: {description: "The window's end (a date = through the end of that day); empty = now.", type: str, default: ''}
  week_start: {description: The weekday a calendar week starts on., type: str, default: monday}
  time_zone: {description: "The time zone of since, until and the calendar weeks (and the report's dates); empty = UTC.", type: str, default: ''}
  max_records: {description: At most this many records per table (the rest are said)., type: int, default: 5000}
  sla: {description: "Read each task's SLAs (task_sla): breached, or close to breaching.", type: bool, default: true}
  sla_warn_percent: {description: "An SLA still running at this share of its time or more is close to breaching.", type: float, default: 75}
  now: {description: The clock (ISO 8601, UTC) - for the tests., type: str, default: ''}
author: site automation
'''

RETURN = r'''
ok: {description: ServiceNow answered and at least one table was read., type: bool, returned: always}
error: {description: Why nothing could be read., type: str, returned: always}
window: {description: "since, until (UTC and in the time zone), period, days, time_zone, label, note."}
groups: {description: "Each group asked for: name, sys_id, found (null when the group table could not be read).", type: list}
users: {description: "Each user asked for: name, sys_id, found.", type: list}
lookup_note: {description: When the groups or users could not be looked up (and how the groups were matched instead)., type: str}
tables: {description: "Each table read: table, rows (kept), read (fetched), error, more (true when max_records cut it).", type: list}
items:
  description: >-
    Each task the week touched - sys_id, number, table, type, link, short_description, state, state_value, priority, assigned_to,
    assignment_group, opened_by, cmdb_ci, category, parent, opened_at, closed_at, resolved_at, updated_at, done_at, done_how,
    close_notes, close_code, active, extra - and the week's verdicts: opened, done, open_at_end, touched, age_days, days_to_done;
    with sla, slas ([{name, stage, breached, running, percent, breach_at}]) and sla_state (breached, at_risk, ok or empty: none),
    sla_name, sla_percent, sla_breach_at (the SLA that says it: a breached one first, else the one furthest on).
  type: list
counts: {description: "opened, done, open_at_end, touched, items, sla_breached, sla_at_risk.", type: dict}
sla: {description: "read (task_sla was read), error, warn_percent, breached, at_risk (tasks).", type: dict}
'''

import datetime
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url

try:
    from zoneinfo import ZoneInfo
except ImportError:            # Python 3.8: UTC only
    ZoneInfo = None

UTC = datetime.timezone.utc
PAGE = 500
# what every task has, and what each table adds
COMMON = ["sys_id", "number", "sys_class_name", "short_description", "state", "priority", "assigned_to", "assignment_group",
          "opened_at", "closed_at", "sys_updated_on", "sys_created_on", "close_notes", "active", "cmdb_ci", "opened_by", "parent"]
TABLE_FIELDS = {
    "incident": ["resolved_at", "close_code", "category", "caller_id", "resolved_by"],
    "sc_task": ["request_item", "request"],
    "change_request": ["type", "risk", "start_date", "end_date", "approval", "category", "close_code"],
    "change_task": ["change_request", "change_task_type"],
    "problem_task": ["problem"],
    "problem": ["resolved_at", "category"],
    "sc_req_item": ["request", "cat_item"],
    "sc_request": ["requested_for"],
}
# the parent a task belongs to, per table (its number is shown)
PARENT_OF = {"sc_task": "request_item", "change_task": "change_request", "problem_task": "problem", "sc_req_item": "request"}
TYPE_LABEL = {"incident": "Incident", "sc_task": "Catalog task", "change_request": "Change", "change_task": "Change task",
              "problem": "Problem", "problem_task": "Problem task", "sc_req_item": "Requested item", "sc_request": "Request",
              "task": "Task"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class SnError(Exception):
    pass


def error_text(e):
    """An HTTPError's body as ServiceNow writes it ({"error": {"message", "detail"}}) -> one line."""
    body = ""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:            # noqa: BLE001
        pass
    try:
        err = json.loads(body).get("error") or {}
        text = ": ".join(x for x in (str(err.get("message") or ""), str(err.get("detail") or "")) if x)
    except (ValueError, AttributeError):
        text = " ".join(body.split())[:200]
    return "HTTP %s%s" % (getattr(e, "code", "?"), (": " + text) if text else "")


class Api(object):
    """The Table API with basic authentication (as roles/servicenow's tasks use it)."""

    def __init__(self, url, username, password, validate_certs=True, ca_path=None, timeout=30):
        self.url = url.rstrip("/")
        self.username, self.password = username, password
        self.validate_certs, self.ca_path, self.timeout = validate_certs, ca_path or None, timeout

    def get(self, path, params):
        full = "%s/%s?%s" % (self.url, path.lstrip("/"), urlencode(params))
        try:
            r = open_url(full, method="GET", url_username=self.username, url_password=self.password, force_basic_auth=True,
                         headers={"Accept": "application/json"}, validate_certs=self.validate_certs, ca_path=self.ca_path,
                         timeout=self.timeout)
            return json.loads(r.read().decode("utf-8", "replace"))
        except HTTPError as e:
            raise SnError("%s %s: %s" % ("GET", path, error_text(e)))
        except URLError as e:
            raise SnError("%s %s: no connection (%s)" % ("GET", path, getattr(e, "reason", e)))
        except (ValueError, OSError) as e:
            raise SnError("%s %s: %s" % ("GET", path, e))


# ---- the window -------------------------------------------------------------------------------------------

def zone(name):
    """A time zone name -> (tzinfo, note). Empty or unknown: UTC (a note says when the name is unknown here)."""
    n = (name or "").strip()
    if not n or n.upper() == "UTC":
        return UTC, ""
    if ZoneInfo is None:
        return UTC, "time zone %s not used: this Python has no zoneinfo; the dates are UTC" % n
    try:
        return ZoneInfo(n), ""
    except Exception:            # noqa: BLE001 - ZoneInfoNotFoundError, or no tzdata
        return UTC, "report_time_zone %s is not a time zone this execution environment knows: the dates are UTC" % n


def parse_when(text, tz, end=False):
    """'YYYY-MM-DD' (end=True: through the end of that day) or 'YYYY-MM-DD HH:MM[:SS]' in tz -> an aware datetime."""
    t = str(text or "").strip().replace("T", " ")
    if not t:
        return None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$", t)
    if not m:
        raise ValueError("%s is not a date (YYYY-MM-DD) or a date and time (YYYY-MM-DD HH:MM)" % text)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if m.group(4) is None:
        dt = datetime.datetime(y, mo, d, tzinfo=tz)
        return dt + datetime.timedelta(days=1) if end else dt
    return datetime.datetime(y, mo, d, int(m.group(4)), int(m.group(5)), int(m.group(6) or 0), tzinfo=tz)


def week_start_of(dt, week_start="monday"):
    """The start (00:00) of dt's calendar week that begins on week_start."""
    ws = WEEKDAYS.index(str(week_start or "monday").strip().lower()) if str(week_start or "monday").strip().lower() in WEEKDAYS else 0
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - datetime.timedelta(days=(day.weekday() - ws) % 7)


def window(period="days", days=7, since="", until="", week_start="monday", time_zone="", now=None):
    """The week -> {since, until (UTC 'YYYY-MM-DD HH:MM:SS'), since_local, until_local, period, days, time_zone, label, note}."""
    tz, note = zone(time_zone)
    now = (now or datetime.datetime.now(UTC)).astimezone(tz)
    p = str(period or "days").strip().lower()
    if since or until:
        p = "dates"
    if p == "dates":
        s, u = parse_when(since, tz), parse_when(until, tz, end=True) or now
        if s is None:
            raise ValueError("period dates needs servicenow_war_since")
    elif p in ("last-week", "last_week", "lastweek"):
        u = week_start_of(now, week_start)
        s = u - datetime.timedelta(days=7)
    elif p in ("this-week", "this_week", "thisweek"):
        s, u = week_start_of(now, week_start), now
    elif p == "days":
        u = now
        s = u - datetime.timedelta(days=float(days or 7))
    else:
        raise ValueError("period %s is not days, last-week, this-week or dates" % period)
    if u <= s:
        raise ValueError("the window ends (%s) before it starts (%s)" % (u.strftime("%Y-%m-%d %H:%M"), s.strftime("%Y-%m-%d %H:%M")))
    su, uu = s.astimezone(UTC), u.astimezone(UTC)
    tzname = str(time_zone or "").strip() if tz is not UTC else "UTC"
    last = u - datetime.timedelta(seconds=1)          # the window's last moment, for a label of whole days
    whole = s.hour == s.minute == 0 and u.hour == u.minute == 0
    label = ("%s to %s" % (s.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"))) if whole else \
        ("%s to %s" % (s.strftime("%Y-%m-%d %H:%M"), u.strftime("%Y-%m-%d %H:%M")))
    return {"since": su.strftime("%Y-%m-%d %H:%M:%S"), "until": uu.strftime("%Y-%m-%d %H:%M:%S"),
            "since_local": s.strftime("%Y-%m-%d %H:%M"), "until_local": u.strftime("%Y-%m-%d %H:%M"),
            "period": p, "days": round((uu - su).total_seconds() / 86400.0, 2), "time_zone": tzname,
            "label": "%s (%s)" % (label, tzname), "note": note}


# ---- the records ---------------------------------------------------------------------------------------------

def sn_time(text):
    """ServiceNow's 'YYYY-MM-DD HH:MM:SS' (UTC) -> an aware datetime, or None."""
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        try:
            return datetime.datetime.strptime(t[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None


def _vd(rec, field):
    """A field of a record read with sysparm_display_value=all -> (value, display)."""
    v = rec.get(field)
    if isinstance(v, dict):
        return str(v.get("value") if v.get("value") is not None else ""), str(v.get("display_value") if v.get("display_value") is not None else "")
    s = "" if v is None else str(v)
    return s, s


def _in(dt, since, until):
    return dt is not None and since <= dt <= until


def normalize(rec, table, url="", extra_fields=None):
    """One record (sysparm_display_value=all) -> the item the report uses (dates as UTC text, people and states as shown)."""
    cls_v, cls_d = _vd(rec, "sys_class_name")
    cls = cls_v or table
    typ = TYPE_LABEL.get(cls) or cls_d or cls
    it = {"sys_id": _vd(rec, "sys_id")[0], "number": _vd(rec, "number")[0] or _vd(rec, "number")[1], "table": cls, "type": typ,
          "short_description": " ".join(_vd(rec, "short_description")[1].split()),
          "state": _vd(rec, "state")[1], "state_value": _vd(rec, "state")[0], "priority": _vd(rec, "priority")[1],
          "assigned_to": _vd(rec, "assigned_to")[1], "assigned_to_id": _vd(rec, "assigned_to")[0],
          "assignment_group": _vd(rec, "assignment_group")[1], "opened_by": _vd(rec, "opened_by")[1],
          "cmdb_ci": _vd(rec, "cmdb_ci")[1], "category": _vd(rec, "category")[1],
          "opened_at": _vd(rec, "opened_at")[0] or _vd(rec, "sys_created_on")[0], "closed_at": _vd(rec, "closed_at")[0],
          "resolved_at": _vd(rec, "resolved_at")[0], "updated_at": _vd(rec, "sys_updated_on")[0],
          "close_notes": " ".join(_vd(rec, "close_notes")[1].split()), "close_code": _vd(rec, "close_code")[1],
          "active": _vd(rec, "active")[0].lower() in ("true", "1"),
          "parent": _vd(rec, PARENT_OF.get(cls, "parent"))[1] or _vd(rec, "parent")[1]}
    if cls == "change_request":
        it["change_type"] = _vd(rec, "type")[1]
        it["approval"] = _vd(rec, "approval")[1]
        it["start_date"], it["end_date"] = _vd(rec, "start_date")[0], _vd(rec, "end_date")[0]
    it["link"] = "%s/nav_to.do?uri=%s.do?sys_id=%s" % (url.rstrip("/"), cls, it["sys_id"]) if url and it["sys_id"] else ""
    it["extra"] = dict((f, _vd(rec, f)[1]) for f in (extra_fields or []))
    return completion(it)


def completion(it):
    """When the task was completed: resolved_at (an incident, a problem - the work was done then; the closure follows by
    itself) else closed_at; the earlier of the two when both are there."""
    done = it.get("resolved_at") or it.get("closed_at") or ""
    if it.get("resolved_at") and it.get("closed_at"):
        a, b = sn_time(it["resolved_at"]), sn_time(it["closed_at"])
        done = it["resolved_at"] if (a and b and a <= b) else it["closed_at"]
    it["done_at"] = done
    it["done_how"] = ("resolved" if done and done == it.get("resolved_at") else "closed") if done else ""
    return it


# what the parent table (task) does not have: read from the class's own table, by sys_id
ENRICH = {"incident": ["resolved_at", "close_code", "category"], "problem": ["resolved_at", "category"]}


def enrich(api, items, chunk=100):
    """Items read from the task table: their resolved_at (and close code, category) from their own table - the parent
    table has no resolved_at, and an incident resolved but not yet closed would look open. -> the tables' errors."""
    errors = []
    for cls, fields in ENRICH.items():
        todo = [it for it in items if it.get("table") == cls and it.get("sys_id") and not it.get("resolved_at")]
        for i in range(0, len(todo), chunk):
            part = todo[i:i + chunk]
            try:
                r = api.get("api/now/table/%s" % cls, {"sysparm_query": "sys_idIN" + ",".join(it["sys_id"] for it in part),
                                                       "sysparm_fields": "sys_id," + ",".join(fields), "sysparm_display_value": "all",
                                                       "sysparm_exclude_reference_link": "true", "sysparm_limit": len(part)})
            except SnError as e:
                errors.append("%s: %s" % (cls, e))
                break
            rows = r.get("result") if isinstance(r, dict) else []
            by_id = dict((_vd(x, "sys_id")[0], x) for x in rows or [])
            for it in part:
                x = by_id.get(it["sys_id"])
                if x is None:
                    continue
                it["resolved_at"] = _vd(x, "resolved_at")[0]
                if "close_code" in fields and not it.get("close_code"):
                    it["close_code"] = _vd(x, "close_code")[1]
                if not it.get("category"):
                    it["category"] = _vd(x, "category")[1]
                completion(it)
    return errors


SLA_FIELDS = ["sys_id", "task", "sla", "stage", "has_breached", "business_percentage", "percentage", "planned_end_time", "active"]


def _pct(v):
    try:
        return round(float(str(v).replace(",", "").strip()), 1)
    except ValueError:
        return None


def sla_of(rows, warn_percent=75):
    """A task's task_sla records (sysparm_display_value=all) -> (slas, state, the SLA that says it). Breached: has_breached.
    Close to breaching (at_risk): not breached, still running (in progress or paused), at warn_percent of its time or more -
    business time where ServiceNow has it (business_percentage), else the clock's."""
    slas = []
    for x in rows:
        stage_v, stage_d = _vd(x, "stage")
        if stage_v.lower() == "cancelled":
            continue
        pct = _pct(_vd(x, "business_percentage")[0])
        if pct is None:
            pct = _pct(_vd(x, "percentage")[0])
        breached = _vd(x, "has_breached")[0].lower() in ("true", "1") or stage_v.lower() == "breached"
        running = stage_v.lower() in ("in_progress", "paused") and _vd(x, "active")[0].lower() not in ("false", "0")
        slas.append({"name": _vd(x, "sla")[1] or _vd(x, "sla")[0], "stage": stage_d or stage_v, "breached": breached, "running": running,
                     "percent": pct, "breach_at": _vd(x, "planned_end_time")[0]})
    if not slas:
        return [], "", None
    bad = [s for s in slas if s["breached"]]
    if bad:
        return slas, "breached", sorted(bad, key=lambda s: s["breach_at"] or "9")[0]
    near = [s for s in slas if s["running"] and (s["percent"] or 0) >= float(warn_percent)]
    if near:
        return slas, "at_risk", sorted(near, key=lambda s: -(s["percent"] or 0))[0]
    return slas, "ok", sorted(slas, key=lambda s: -(s["percent"] or 0))[0]


def read_slas(api, items, warn_percent=75, chunk=100):
    """Each item's SLAs from task_sla (by its sys_id) into the item -> the error ('' when read)."""
    by_task = {}
    ids = [it["sys_id"] for it in items if it.get("sys_id")]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/task_sla", {"sysparm_query": "taskIN" + ",".join(part) + "^stage!=cancelled",
                                                   "sysparm_fields": ",".join(SLA_FIELDS), "sysparm_display_value": "all",
                                                   "sysparm_exclude_reference_link": "true", "sysparm_limit": 20 * len(part)})
        except SnError as e:
            return str(e)
        for x in (r.get("result") if isinstance(r, dict) else None) or []:
            by_task.setdefault(_vd(x, "task")[0], []).append(x)
    for it in items:
        slas, state, top = sla_of(by_task.get(it.get("sys_id"), []), warn_percent)
        it["slas"], it["sla_state"] = slas, state
        it["sla_name"] = top["name"] if top else ""
        it["sla_percent"] = top["percent"] if top else None
        it["sla_breach_at"] = top["breach_at"] if top else ""
    return ""


def classify(it, since, until):
    """The week's verdicts on an item, on its UTC timestamps: opened, done, open_at_end, touched, age_days, days_to_done.
    None when the week never touched it."""
    o, d, u = sn_time(it.get("opened_at")), sn_time(it.get("done_at")), sn_time(it.get("updated_at"))
    opened = _in(o, since, until)
    done = _in(d, since, until)
    open_end = o is not None and o <= until and (d is None or d > until)
    # worked on: updated in the week, still open at its end, not new (one completed before the week and closed by itself
    # in it is nothing the week did)
    touched = _in(u, since, until) and open_end and not opened
    if not (opened or done or open_end):
        return None
    out = dict(it)
    out.update({"opened": opened, "done": done, "open_at_end": open_end, "touched": touched,
                "age_days": round((until - o).total_seconds() / 86400.0, 1) if (open_end and o) else None,
                "days_to_done": round((d - o).total_seconds() / 86400.0, 1) if (d and o and d >= o) else None})
    return out


def table_query(group_ids, user_ids, since, until, margin_days=1, group_names=None):
    """The encoded query of one table: the groups (by sys_id, or by name when the group table could not be read) or the
    users, and anything the week may have touched - a margin of a day on each side, since ServiceNow reads a literal date
    in the API account's time zone; the exact sort is made here on the records' UTC timestamps. ^OR joins the condition
    before it: a^ORb^c = (a or b) and c."""
    m = datetime.timedelta(days=margin_days)
    s, u = (since - m).strftime("%Y-%m-%d %H:%M:%S"), (until + m).strftime("%Y-%m-%d %H:%M:%S")
    who = []
    if group_ids:
        who.append("assignment_groupIN" + ",".join(group_ids))
    elif group_names:
        who.append("assignment_group.nameIN" + ",".join(group_names))
    if user_ids:
        who.append("assigned_toIN" + ",".join(user_ids))
    q = "^OR".join(who)
    q += "^opened_at<=" + u
    q += "^closed_at>=" + s + "^ORclosed_atISEMPTY^ORsys_updated_on>=" + s
    q += "^ORDERBYopened_at"
    return q


def read_table(api, table, query, extra_fields=None, max_records=5000, page=PAGE):
    """Every record of a table for the query, page by page -> (records, more: bool - max_records cut it)."""
    fields = COMMON + TABLE_FIELDS.get(table, []) + [f for f in (extra_fields or []) if f]
    if table != "task" and table not in TABLE_FIELDS:
        fields = COMMON + [f for f in (extra_fields or []) if f]
    out, offset = [], 0
    while True:
        limit = min(page, max_records - len(out))
        if limit <= 0:
            return out, True
        r = api.get("api/now/table/%s" % table, {"sysparm_query": query, "sysparm_fields": ",".join(fields), "sysparm_display_value": "all",
                                                 "sysparm_exclude_reference_link": "true", "sysparm_limit": limit, "sysparm_offset": offset})
        rows = r.get("result") if isinstance(r, dict) else None
        if not isinstance(rows, list):
            raise SnError("GET api/now/table/%s: no result list in the answer" % table)
        out += rows
        if len(rows) < limit:
            return out, False
        offset += len(rows)


def lookup(api, table, names, name_fields):
    """The sys_ids of records named (a group by name; a user by user name or full name) -> ([{name, sys_id, found}], error)."""
    want = [str(n).strip() for n in names or [] if str(n).strip()]
    if not want:
        return [], ""
    q = "^OR".join("%sIN%s" % (f, ",".join(want)) for f in name_fields)
    try:
        r = api.get("api/now/table/%s" % table, {"sysparm_query": q, "sysparm_fields": "sys_id," + ",".join(name_fields),
                                                 "sysparm_limit": max(50, 2 * len(want))})
    except SnError as e:
        return [{"name": n, "sys_id": "", "found": None} for n in want], str(e)
    rows = r.get("result") if isinstance(r, dict) else []
    out = []
    for n in want:
        hit = next((x for x in rows or [] if any(str(x.get(f, "")).strip().lower() == n.lower() for f in name_fields)), None)
        out.append({"name": n, "sys_id": str(hit.get("sys_id", "")) if hit else "", "found": bool(hit)})
    return out, ""


def read_week(api, groups, users, tables, win, extra_fields=None, max_records=5000, sla=True, sla_warn_percent=75):
    """Everything the week touched for the groups (and users): the lookups, the tables, the items sorted. -> the module's result."""
    since, until = sn_time(win["since"]), sn_time(win["until"])
    res = {"ok": False, "error": "", "window": win, "groups": [], "users": [], "tables": [], "items": [], "counts": {}, "lookup_note": "",
           "sla": {"read": False, "error": "", "warn_percent": sla_warn_percent, "breached": 0, "at_risk": 0}}
    g, gerr = lookup(api, "sys_user_group", groups, ["name"])
    res["groups"] = g
    u, uerr = lookup(api, "sys_user", users, ["user_name", "name"])
    res["users"] = u
    gids = [x["sys_id"] for x in g if x.get("sys_id")]
    uids = [x["sys_id"] for x in u if x.get("sys_id")]
    gnames = []
    if gerr and groups:
        # the group table cannot be read (an account without read on sys_user_group): the names go as they are, and
        # ServiceNow matches them itself (assignment_group.name); a group that is not there then simply has nothing
        gnames = [x["name"] for x in g]
        res["lookup_note"] = "the assignment groups were not looked up (%s): matched by name instead" % gerr
    if uerr and users:
        res["lookup_note"] = (res.get("lookup_note", "") + " " if res.get("lookup_note") else "") + \
            "the users were not looked up (%s): the API account needs read on sys_user" % uerr
    if not gids and not uids and not gnames:
        res["error"] = ("none of the assignment groups is in ServiceNow: %s" % ", ".join(x["name"] for x in g)) if g else \
            ("no assignment group: set servicenow_war_groups" if not users else "none of the users is in ServiceNow: %s" % ", ".join(x["name"] for x in u))
        return res
    query = table_query(gids, uids, since, until, group_names=gnames)
    seen, items = set(), []
    for t in [str(x).strip() for x in tables or [] if str(x).strip()]:
        entry = {"table": t, "rows": 0, "read": 0, "error": "", "more": False}
        try:
            rows, more = read_table(api, t, query, extra_fields, max_records)
        except SnError as e:
            entry["error"] = str(e)
            res["tables"].append(entry)
            continue
        entry["read"], entry["more"] = len(rows), more
        found = []
        for rec in rows:
            it = normalize(rec, t, api.url, extra_fields)
            if not it["sys_id"] or it["sys_id"] in seen:
                continue
            seen.add(it["sys_id"])
            found.append(it)
        if t == "task":
            for e in enrich(api, found):
                entry["error"] = (entry["error"] + "; " if entry["error"] else "") + "resolved_at not read from " + e
        for it in found:
            c = classify(it, since, until)
            if c is None:
                continue
            items.append(c)
            entry["rows"] += 1
        if entry["error"] and entry["read"]:
            entry["error"] += " (its records are reported with their closed_at only)"
        res["tables"].append(entry)
    items.sort(key=lambda x: (x.get("done_at") or "9", x.get("opened_at") or "", x.get("number") or ""))
    res["items"] = items
    if sla and items:
        err = read_slas(api, items, sla_warn_percent)
        res["sla"].update({"read": not err, "error": err,
                           "breached": sum(1 for x in items if x.get("sla_state") == "breached"),
                           "at_risk": sum(1 for x in items if x.get("sla_state") == "at_risk")})
    elif sla:
        res["sla"]["read"] = True
    res["counts"] = {"items": len(items), "opened": sum(1 for x in items if x["opened"]), "done": sum(1 for x in items if x["done"]),
                     "open_at_end": sum(1 for x in items if x["open_at_end"]), "touched": sum(1 for x in items if x["touched"]),
                     "sla_breached": res["sla"]["breached"], "sla_at_risk": res["sla"]["at_risk"]}
    read_ok = [x for x in res["tables"] if not x["error"] or x["read"]]
    res["ok"] = len(read_ok) > 0
    if not res["ok"]:
        res["error"] = "no table could be read: " + "; ".join("%s - %s" % (x["table"], x["error"]) for x in res["tables"]) if res["tables"] \
            else "no table to read: set servicenow_war_tables"
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(
            url=dict(type="str", required=True), username=dict(type="str", required=True), password=dict(type="str", required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), ca_path=dict(type="path"), timeout=dict(type="int", default=30),
            groups=dict(type="list", elements="str", default=[]), users=dict(type="list", elements="str", default=[]),
            tables=dict(type="list", elements="str", default=["incident", "sc_task", "change_request", "change_task", "problem_task"]),
            fields_extra=dict(type="list", elements="str", default=[]),
            period=dict(type="str", default="days"), days=dict(type="float", default=7), since=dict(type="str", default=""),
            until=dict(type="str", default=""), week_start=dict(type="str", default="monday"), time_zone=dict(type="str", default=""),
            max_records=dict(type="int", default=5000), now=dict(type="str", default=""),
            sla=dict(type="bool", default=True), sla_warn_percent=dict(type="float", default=75),
        ),
        supports_check_mode=True)
    p = module.params
    now = None
    if p["now"]:
        try:
            now = datetime.datetime.strptime(p["now"].replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            module.fail_json(msg="now must be YYYY-MM-DDTHH:MM:SS (UTC), not %s" % p["now"])
    try:
        win = window(p["period"], p["days"], p["since"], p["until"], p["week_start"], p["time_zone"], now)
    except ValueError as e:
        module.fail_json(msg="the week: %s" % e)
    url = p["url"].strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    api = Api(url, p["username"], p["password"], p["validate_certs"], p["ca_path"], p["timeout"])
    try:
        res = read_week(api, p["groups"], p["users"], p["tables"], win, p["fields_extra"], p["max_records"], p["sla"], p["sla_warn_percent"])
    except SnError as e:
        res = {"ok": False, "error": str(e), "window": win, "groups": [], "users": [], "tables": [], "items": [], "counts": {}, "lookup_note": ""}
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
