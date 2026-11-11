#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Ticket insights (roles/servicenow_insights): months of ServiceNow records read for the report - incidents, requested
items, catalog tasks, changes, change tasks, problems - in the window and the window before it, and anything still open; and the
assignment history of the incidents passed between groups (sys_audit). Read-only. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_servicenow_insights
short_description: Read months of ServiceNow tickets for the ticket insights report (read-only)
description:
  - Reads each table (Table API, sysparm_display_value=all) for the records opened, closed or resolved in the window or
    the one before it (compare), and those still open - of the groups named, or every group.
  - Each record is kept with the fields the report uses (people and states as ServiceNow shows them, dates as UTC text).
  - With audit, reads sys_audit for the assignment group history of the incidents reassigned handoff_min times or more.
  - With members, reads sys_user_grmember for the members of every assignment group the records name (the teams'
    leaderboards list the members; a ticket resolved by someone outside the team counts for the member it was assigned to).
  - A table (or sys_audit, sys_user_grmember) the account cannot read is returned with its error; the others are still read.
  - With group_parents (0.97.0), reads sys_user_group for the groups the records name and their parents up the chain
    (sys_id, name, parent, active, type - never a person's field): the sites and directorates ServiceNow itself knows.
  - With managers (1.15.0), the same read of sys_user_group also asks each group's manager, dot-walked (manager.name,
    manager.email, manager.active) - the team leads; with group_parents off, the groups the records name are read for it alone.
  - With work_notes_for (0.96.0), reads only the work notes of those incidents (sys_journal_field, element work_notes,
    newest first, GET only) for ACT's fixes - nothing else; refused (403) is returned as work_notes_error.
options:
  url: {description: The instance's address (SN_HOST)., type: str, required: true}
  username: {description: The API account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  validate_certs: {description: Check the instance's certificate., type: bool, default: true}
  ca_path: {description: A CA file for the instance., type: path}
  timeout: {description: Seconds per request., type: int, default: 30}
  groups: {description: "The assignment groups (exact names); [] = every group.", type: list, elements: str, default: []}
  tables: {description: The tables read., type: list, elements: str, default: [incident, sc_req_item, sc_task, change_request, change_task, problem]}
  months: {description: The window - this many months up to until., type: int, default: 6}
  days: {description: "The window in days (> 0 wins over months).", type: float, default: 0}
  until: {description: "The window's last day (through its end); '' = now; month_start - the end of last month (the backfill, 1.13.0).", type: str, default: ''}
  compare: {description: Read the window before as well (the same length)., type: bool, default: true}
  time_zone: {description: "The zone of the dates given and of the window's whole days ('' = UTC).", type: str, default: ''}
  max_records: {description: At most this many records per table., type: int, default: 20000}
  audit: {description: Read the assignment history (sys_audit) of the incidents passed around., type: bool, default: true}
  handoff_min: {description: The reassignments that make an incident passed around., type: int, default: 3}
  handoff_source: {description: "Where the hand-off paths come from (0.97.0): auto (sys_audit, else metric_instance), audit, metric (never sys_audit), none.", type: str, default: auto}
  members: {description: Read the groups' members (sys_user_grmember) for the leaderboards., type: bool, default: true}
  group_parents: {description: "Read the groups' parents and active flag (sys_user_group) for the sites and directorates.", type: bool, default: true}
  managers: {description: "Read each group's manager (sys_user_group.manager: name, email, active) in the same read - the team leads.", type: bool, default: false}
  now: {description: "The clock (tests): YYYY-MM-DDTHH:MM:SS UTC.", type: str, default: ''}
  work_notes_for: {description: "Incident sys_ids: read only their work notes (sys_journal_field), nothing else.", type: list, elements: str, default: []}
  work_notes_max: {description: The newest work notes kept per incident., type: int, default: 20}
  work_note_chars: {description: Each work note cut to this many characters., type: int, default: 1500}
  sla_source: {description: "Where met or missed comes from (1.7.0): auto (task_sla when the account reads it, else targets), task_sla,
      targets (the report's own targets), made_sla (ServiceNow's Made SLA flag).", type: str, default: auto}
  first_assigned: {description: "With the targets: read when each incident done in the window was first assigned to a person
      (metric_instance, the metric Assigned to) - the response's end.", type: bool, default: false}
  state_metric: {description: "The metric_instance definition of the incidents' state durations (1.7.0: the time on hold); '' = not read.",
      type: str, default: Incident State Duration}
  survey: {description: "Read the incident surveys' results (asmt_metric_result, GET; best-effort): the window's and the window before's.", type: bool, default: true}
  survey_metrics: {description: "The survey metrics read, by name ([] = every metric whose answer is a number).", type: list, elements: str, default: []}
  survey_types: {description: "The surveys read, by name (asmt_metric_type; [] = every survey; 1.16.0).", type: list, elements: str, default: []}
author: site automation
'''

RETURN = r'''
ok: {description: At least one table was read., type: bool}
error: {description: Why nothing could be read., type: str}
window: {description: "since, until, prev_since (UTC text), since_local, until_local, label, prev_label, days, time_zone, note.", type: dict}
groups: {description: "[{name, sys_id, found}] - the groups named.", type: list}
tables: {description: "[{table, read, kept, more, error}]", type: list}
items: {description: "The records: table, number, sys_id, short_description, state, priority, people, group, dates, close notes ...", type: list}
audit: {description: "{incident sys_id: [[when, old group, new group], ...]} (group names), oldest first.", type: dict}
audit_error: {description: "Why the hand-off paths were not read ('' = read, or not needed) - each table tried.", type: str}
audit_source: {description: "Where the paths came from: sys_audit, metric_instance, or '' (counts only).", type: str}
audit_note: {description: "sys_audit refused but metric_instance read: why (for the report's Not read).", type: str}
members: {description: "{group name: [the members' names]} for every group the records name (sys_user_grmember).", type: dict}
members_error: {description: "Why sys_user_grmember was not read ('' = read, or not asked).", type: str}
group_tree: {description: "{sys_id: {name, parent (sys_id), active, type}} - the groups the records name and their parents.", type: dict}
group_tree_error: {description: "Why sys_user_group was not read ('' = read, or not asked).", type: str}
managers: {description: "{group name: {name, email, active}} - the manager of each group the records name that has one (with managers).", type: dict}
managers_error: {description: "Why the managers were not read ('' = read, or not asked).", type: str}
url: {description: The instance's address., type: str}
work_notes: {description: "With work_notes_for: {incident sys_id: [[when (UTC text), text], ...]}, newest first.", type: dict}
work_notes_error: {description: "Why sys_journal_field was not read ('' = read).", type: str}
sla: {description: "{source (task_sla, targets, made_sla), asked, error, note, records: {sys_id: [{name, stage, breached, percent, paused_at}]},
  paused: {sys_id: when its SLA was paused}, paused_error} - task_sla read for the incidents and requested items done in the window and the
  one before (cancelled left out), and for the incidents on hold now.", type: dict}
first_assigned: {description: "{incident sys_id: UTC text} - when it was first assigned to a person (the metric Assigned to).", type: dict}
first_assigned_error: {description: "Why the metric Assigned to was not read ('' = read, or not asked).", type: str}
states: {description: "{incident sys_id: [[state as shown, start, end]]} - the state metric of the incidents on hold now and those done in the window.", type: dict}
states_error: {description: "Why the state metric was not read ('' = read; 'none' = no such metric).", type: str}
survey: {description: "[[incident sys_id, metric, value (a number), taken (UTC text)]] - the incident surveys' answers.", type: list}
survey_error: {description: "Why the surveys were not read ('' = read, or not asked).", type: str}
survey_questions: {description: "1.16.0 - each question read (its key in survey): name, survey, state, datatype, min, max.", type: dict}
'''

import calendar
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
PAGE = 1000
COMMON = ["sys_id", "number", "short_description", "state", "priority", "assigned_to", "assignment_group", "opened_at", "closed_at",
          "closed_by", "close_notes", "active", "cmdb_ci", "opened_by", "made_sla", "reassignment_count", "sys_created_on", "sys_updated_on"]
TABLE_FIELDS = {
    "incident": ["resolved_at", "resolved_by", "close_code", "category", "subcategory", "caller_id", "reopen_count", "caused_by",
                 "problem_id", "impact", "urgency", "hold_reason"],
    "sc_req_item": ["cat_item", "requested_for"],
    "sc_task": ["request_item"],
    "change_request": ["type", "close_code", "start_date", "end_date", "work_start", "work_end", "category"],
    # 1.2.0: a change's tasks - close_code where the instance has it (a field it does not have comes back empty)
    "change_task": ["change_request", "planned_start_date", "planned_end_date", "close_code"],
    "problem": ["resolved_at", "category"],
}
# the tables whose records have a resolved_at besides closed_at
RESOLVES = ("incident", "problem")
# 1.17.0, the ticket store (servicenow_insights_incremental): the light read every run asks only these - when each ticket
# changed, and the text, the people and the CI, which the report database never holds; the rest of an unchanged ticket comes
# from the database. LIGHT_KEYS: the item's keys made from them (never stored). 1.17.0b: the names ServiceNow shows that can
# change without the ticket's sys_updated_on moving - the group's name (a group renamed), the choices' labels (state,
# priority, category, close code, hold reason, impact, urgency, a change's type), the catalog item's name - are read live
# too (labels): the store keeps the group's sys_id, never its name.
LIGHT_COMMON = ["sys_id", "sys_updated_on", "opened_at", "sys_created_on", "short_description", "assigned_to", "opened_by", "closed_by",
                "close_notes", "cmdb_ci", "assignment_group", "state", "priority"]
LIGHT_TABLE = {"incident": ["resolved_by", "caller_id", "close_code", "category", "subcategory", "hold_reason", "impact", "urgency"],
               "sc_req_item": ["requested_for", "cat_item"], "change_request": ["type", "close_code", "category"],
               "change_task": ["close_code"], "problem": ["category"]}
LABEL_KEYS = ("state", "priority", "priority_value", "group", "group_id", "close_code", "category", "subcategory", "hold_reason",
              "impact_value", "urgency_value", "cat_item", "change_type", "change_type_value")
LIGHT_KEYS = ("short_description", "assigned_to", "opened_by", "closed_by", "close_notes", "cmdb_ci", "resolved_by", "caller",
              "caller_id") + LABEL_KEYS
# a stored row's format: a row of another format is read again from ServiceNow (2: 1.17.0b, without the labels)
STORE_CODE = "2"


class SnError(Exception):
    pass


def error_text(e):
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
    """The Table API with basic authentication."""

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
            raise SnError("GET %s: %s" % (path, error_text(e)))
        except URLError as e:
            raise SnError("GET %s: no connection (%s)" % (path, getattr(e, "reason", e)))
        except (ValueError, OSError) as e:
            raise SnError("GET %s: %s" % (path, e))


# ---- the window -------------------------------------------------------------------------------------------

def zone(name):
    n = (name or "").strip()
    if not n or n.upper() == "UTC":
        return UTC, ""
    if ZoneInfo is None:
        return UTC, "time zone %s not used: this Python has no zoneinfo; the dates are UTC" % n
    try:
        return ZoneInfo(n), ""
    except Exception:            # noqa: BLE001
        return UTC, "report_time_zone %s is not a time zone this execution environment knows: the dates are UTC" % n


def months_back(dt, months):
    """dt moved back by whole calendar months (the day kept, or the month's last day)."""
    y, m = dt.year, dt.month - int(months)
    while m < 1:
        m += 12
        y -= 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)


def window(months=6, days=0, until="", compare=True, time_zone="", now=None):
    """The window -> {since, until, prev_since (UTC text), since_local, until_local, label, prev_label, days, time_zone, note}.
    It starts at the beginning of a day; it ends now, or at the end of the day until names."""
    tz, note = zone(time_zone)
    now = (now or datetime.datetime.now(UTC)).astimezone(tz)
    u = now
    t = str(until or "").strip()
    if t == "month_start":           # the backfill (1.13.0): whole calendar months, up to this month's first day
        u = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        t = ""
    if t:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", t)
        if not m:
            raise ValueError("servicenow_insights_until %s is not a date (YYYY-MM-DD)" % until)
        u = datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=tz) + datetime.timedelta(days=1)
    day = u.replace(hour=0, minute=0, second=0, microsecond=0)
    if float(days or 0) > 0:
        s = day - datetime.timedelta(days=float(days)) + (datetime.timedelta(days=1) if u != day else datetime.timedelta(0))
    else:
        if int(months or 0) < 1:
            raise ValueError("servicenow_insights_months must be 1 or more (or servicenow_insights_days > 0)")
        s = months_back(day if u == day else day + datetime.timedelta(days=1), months)
    if u <= s:
        raise ValueError("the window ends (%s) before it starts (%s)" % (u.strftime("%Y-%m-%d %H:%M"), s.strftime("%Y-%m-%d %H:%M")))
    length = u - s
    # the window before: the same months (calendar months) or the same number of days
    ps = (s if not compare else (months_back(s, months) if not float(days or 0) > 0 else s - length))
    su, uu, psu = s.astimezone(UTC), u.astimezone(UTC), ps.astimezone(UTC)
    tzname = str(time_zone or "").strip() if tz is not UTC else "UTC"

    def label(a, b):
        last = b - datetime.timedelta(seconds=1)
        return "%s to %s" % (a.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"))

    return {"since": su.strftime("%Y-%m-%d %H:%M:%S"), "until": uu.strftime("%Y-%m-%d %H:%M:%S"),
            "prev_since": psu.strftime("%Y-%m-%d %H:%M:%S"), "compare": bool(compare),
            "since_local": s.strftime("%Y-%m-%d %H:%M"), "until_local": u.strftime("%Y-%m-%d %H:%M"),
            "days": round(length.total_seconds() / 86400.0, 1), "time_zone": tzname, "note": note,
            "label": label(s, u), "prev_label": label(ps, s) if compare else "",
            "months": int(months or 0) if not float(days or 0) > 0 else 0}


# ---- the records ---------------------------------------------------------------------------------------------

def _vd(rec, field):
    """A field read with sysparm_display_value=all -> (value, display)."""
    v = rec.get(field)
    if isinstance(v, dict):
        return (str(v.get("value") if v.get("value") is not None else ""),
                str(v.get("display_value") if v.get("display_value") is not None else ""))
    s = "" if v is None else str(v)
    return s, s


def _int(text):
    try:
        return int(float(str(text).replace(",", "").strip() or 0))
    except ValueError:
        return 0


def _flag(text):
    t = str(text or "").strip().lower()
    return True if t in ("true", "1", "yes") else (False if t in ("false", "0", "no") else None)


def _prio(text):
    """A priority ('1', or '1 - Critical' as shown) -> its number as text ('' = none)."""
    m = re.match(r"\s*(\d)", str(text or ""))
    return m.group(1) if m else ""


def labels(rec, table):
    """The item's names as ServiceNow shows them now (1.17.0b, LABEL_KEYS): the group (and its sys_id), the choices'
    labels, the catalog item - read live every run, never kept in the ticket store."""
    d = lambda f: " ".join(_vd(rec, f)[1].split())          # noqa: E731
    v = lambda f: _vd(rec, f)[0]                            # noqa: E731
    it = {"state": d("state"), "priority": d("priority"), "priority_value": _prio(v("priority") or d("priority")),
          "group": d("assignment_group"), "group_id": v("assignment_group")}
    if table == "incident":
        it.update({"close_code": d("close_code"), "category": d("category"), "subcategory": d("subcategory"), "hold_reason": d("hold_reason"),
                   "impact_value": _prio(v("impact") or d("impact")), "urgency_value": _prio(v("urgency") or d("urgency"))})
    elif table == "sc_req_item":
        it["cat_item"] = d("cat_item")
    elif table == "change_request":
        it.update({"change_type": d("type"), "change_type_value": v("type").lower(), "close_code": d("close_code"), "category": d("category")})
    elif table == "change_task":
        it["close_code"] = d("close_code")
    elif table == "problem":
        it["category"] = d("category")
    return it


def light(rec, table):
    """The item's keys from the light read's fields (1.17.0, LIGHT_KEYS): the text, the people and the CI as shown, and
    (1.17.0b) the names ServiceNow shows (labels)."""
    d = lambda f: " ".join(_vd(rec, f)[1].split())          # noqa: E731
    it = {"short_description": d("short_description"), "assigned_to": d("assigned_to"), "opened_by": d("opened_by"),
          "cmdb_ci": d("cmdb_ci"), "closed_by": d("closed_by"), "close_notes": d("close_notes")}
    if table == "incident":
        it.update({"resolved_by": d("resolved_by"), "caller": d("caller_id"), "caller_id": _vd(rec, "caller_id")[0]})
    elif table == "sc_req_item":
        it["caller"] = d("requested_for")
    it.update(labels(rec, table))
    return it


def normalize(rec, table):
    """One record -> the item the report uses: only the fields it reads, people and states as shown, dates as UTC text."""
    d = lambda f: " ".join(_vd(rec, f)[1].split())          # noqa: E731
    v = lambda f: _vd(rec, f)[0]                            # noqa: E731
    lt = light(rec, table)
    it = {"table": table, "sys_id": v("sys_id"), "number": v("number") or d("number"),
          "short_description": lt["short_description"], "state": lt["state"], "priority": lt["priority"],
          "priority_value": lt["priority_value"],
          "assigned_to": lt["assigned_to"], "group": lt["group"],
          "group_id": lt["group_id"], "opened_by": lt["opened_by"], "cmdb_ci": lt["cmdb_ci"],
          "opened_at": v("opened_at") or v("sys_created_on"), "closed_at": v("closed_at"), "closed_by": lt["closed_by"],
          "close_notes": lt["close_notes"], "active": _flag(v("active")), "made_sla": _flag(v("made_sla")),
          "reassignment_count": _int(v("reassignment_count"))}
    if table in RESOLVES:
        it["resolved_at"] = v("resolved_at")
    if table == "incident":
        it.update({"resolved_by": lt["resolved_by"], "close_code": lt["close_code"], "category": lt["category"],
                   "subcategory": lt["subcategory"], "caller": lt["caller"], "caller_id": lt["caller_id"],
                   "reopen_count": _int(v("reopen_count")), "caused_by": d("caused_by"), "problem": d("problem_id"),
                   "impact_value": lt["impact_value"], "urgency_value": lt["urgency_value"],
                   "hold_reason": lt["hold_reason"]})
    elif table == "sc_req_item":
        it.update({"cat_item": lt["cat_item"], "caller": lt["caller"]})
    elif table == "sc_task":
        it["parent"] = d("request_item")
    elif table == "change_request":
        it.update({"change_type": lt["change_type"], "change_type_value": lt["change_type_value"], "close_code": lt["close_code"],
                   "start_date": v("work_start") or v("start_date"), "end_date": v("work_end") or v("end_date"), "category": lt["category"]})
    elif table == "change_task":
        it.update({"parent": d("change_request"), "parent_id": v("change_request"), "close_code": lt["close_code"],
                   "planned_start": v("planned_start_date"), "planned_end": v("planned_end_date")})
    elif table == "problem":
        it["category"] = lt["category"]
    # when the work was done: resolved (incidents, problems), else closed
    it["done_at"] = it.get("resolved_at") or it.get("closed_at") or ""
    if it.get("resolved_at") and it.get("closed_at") and it["closed_at"] < it["resolved_at"]:
        it["done_at"] = it["closed_at"]
    return it


def table_query(table, group_ids, group_names, since, until, margin_days=1):
    """Every record of the groups (or every group) opened, closed or resolved in [since, until] - since is the window
    before's start - or still open; a margin of a day on each side (ServiceNow reads a literal date in the API account's
    zone; the exact sort is made on the records' UTC times). ^OR joins the condition before it: a^ORb^c = (a or b) and c."""
    m = datetime.timedelta(days=margin_days)
    s, u = (since - m).strftime("%Y-%m-%d %H:%M:%S"), (until + m).strftime("%Y-%m-%d %H:%M:%S")
    q = []
    if group_ids:
        q.append("assignment_groupIN" + ",".join(group_ids))
    elif group_names:
        q.append("assignment_group.nameIN" + ",".join(group_names))
    when = "opened_at>=%s^ORclosed_at>=%s^ORactive=true" % (s, s)
    if table in RESOLVES:
        when += "^ORresolved_at>=" + s
    q.append(when)
    q.append("opened_at<=" + u)
    # newest first (1.16.0): a table over max_records keeps its newest records - the window whole before the one before
    return "^".join(q) + "^ORDERBYDESCopened_at"


def older_query(table, group_ids, group_names, since_w, cut, margin_days=1):
    """1.16.0: after a capped read (newest first, cut at the oldest opened_at kept), the records opened up to the cut
    that still count in the window itself: still open, or closed / resolved in the window (the first read's ones again
    are dropped by their sys_id)."""
    m = datetime.timedelta(days=margin_days)
    s = (since_w - m).strftime("%Y-%m-%d %H:%M:%S")
    q = []
    if group_ids:
        q.append("assignment_groupIN" + ",".join(group_ids))
    elif group_names:
        q.append("assignment_group.nameIN" + ",".join(group_names))
    when = "active=true^ORclosed_at>=" + s
    if table in RESOLVES:
        when += "^ORresolved_at>=" + s
    q.append(when)
    q.append("opened_at<=" + cut.strftime("%Y-%m-%d %H:%M:%S"))       # <=: the records opened in the cut's own second too
    return "^".join(q) + "^ORDERBYDESCopened_at"


def count_records(api, table, query):
    """How many records a query matches (the Aggregate API) - None when it can not be read."""
    try:
        r = api.get("api/now/stats/%s" % table, {"sysparm_query": query.split("^ORDERBY")[0], "sysparm_count": "true"})
        return int(((r or {}).get("result") or {}).get("stats", {}).get("count"))
    except (SnError, TypeError, ValueError, AttributeError):
        return None


def read_table(api, table, query, max_records=20000, page=PAGE, fields=None):
    """At most max_records records -> (records, more). One record more than the cap is asked for (1.16.0), so a table of
    exactly max_records is not said to be cut. fields: the light read's (1.17.0); else every field the report uses."""
    fields = fields or COMMON + TABLE_FIELDS.get(table, [])
    out, offset, want = [], 0, int(max_records) + 1
    while len(out) < want:
        limit = min(page, want - len(out))
        r = api.get("api/now/table/%s" % table, {"sysparm_query": query, "sysparm_fields": ",".join(fields), "sysparm_display_value": "all",
                                                 "sysparm_exclude_reference_link": "true", "sysparm_limit": limit, "sysparm_offset": offset})
        rows = r.get("result") if isinstance(r, dict) else None
        if not isinstance(rows, list):
            raise SnError("GET api/now/table/%s: no result list in the answer" % table)
        out += rows
        if len(rows) < limit:
            break
        offset += len(rows)
    return out[:int(max_records)], len(out) > int(max_records)


def lookup_groups(api, names):
    want = [str(n).strip() for n in names or [] if str(n).strip()]
    if not want:
        return [], ""
    try:
        r = api.get("api/now/table/sys_user_group", {"sysparm_query": "nameIN" + ",".join(want), "sysparm_fields": "sys_id,name",
                                                     "sysparm_limit": max(50, 2 * len(want))})
    except SnError as e:
        return [{"name": n, "sys_id": "", "found": None} for n in want], str(e)
    rows = r.get("result") if isinstance(r, dict) else []
    out = []
    for n in want:
        hit = next((x for x in rows or [] if str(x.get("name", "")).strip().lower() == n.lower()), None)
        out.append({"name": n, "sys_id": str(hit.get("sys_id", "")) if hit else "", "found": bool(hit)})
    return out, ""


def read_audit(api, ids, names, chunk=100):
    """The assignment group changes of these incidents (sys_audit) -> ({sys_id: [[when, old, new]]}, error). The values
    are sys_ids: shown by the group names the records carry (names: sys_id -> name)."""
    out = {}
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/sys_audit", {"sysparm_query": "tablename=incident^fieldname=assignment_group^documentkeyIN" + ",".join(part)
                                                    + "^ORDERBYsys_created_on",
                                                    "sysparm_fields": "documentkey,oldvalue,newvalue,sys_created_on",
                                                    "sysparm_limit": 50 * len(part)})
        except SnError as e:
            return out, str(e)
        for x in (r.get("result") if isinstance(r, dict) else None) or []:
            k = str(x.get("documentkey") or "")
            old, new = str(x.get("oldvalue") or ""), str(x.get("newvalue") or "")
            out.setdefault(k, []).append([str(x.get("sys_created_on") or ""), names.get(old, old), names.get(new, new)])
    for k in out:
        out[k].sort(key=lambda e: e[0])
    return out, ""


def read_metric(api, ids, names, chunk=100):
    """The assignment group sequence of these incidents from the out-of-box metric "Assignment Group" (metric_instance,
    GET only: id, value, start, end) -> ({sys_id: [[when, old, new]]} as read_audit gives it, error)."""
    rows = {}
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/metric_instance", {"sysparm_query": "definition.name=Assignment Group^table=incident^idIN" + ",".join(part)
                                                          + "^ORDERBYstart", "sysparm_fields": "id,value,start,end",
                                                          "sysparm_limit": 30 * len(part)})
        except SnError as e:
            return {}, str(e)
        for x in (r.get("result") if isinstance(r, dict) else None) or []:
            k = str(_vd(x, "id")[0] or "")
            v = str(_vd(x, "value")[1] or _vd(x, "value")[0] or "").strip()
            if k and v:
                rows.setdefault(k, []).append([str(_vd(x, "start")[0] or ""), names.get(v, v)])
    out = {}
    for k, seq in rows.items():
        seq.sort(key=lambda e: e[0])
        out[k] = [[w, seq[j - 1][1] if j else "", g] for j, (w, g) in enumerate(seq)]
    return out, ""


def read_paths(api, ids, names, source="auto"):
    """The hand-off paths (0.97.0): sys_audit, else metric_instance (auto); one of them (audit, metric); none ->
    (paths, source, error, note)."""
    src = str(source or "auto").strip().lower()
    if src == "none" or not ids:
        return {}, "", "", ""
    errs = []
    if src in ("auto", "audit"):
        got, err = read_audit(api, ids, names)
        if not err:
            return got, "sys_audit", "", ""
        errs.append("sys_audit: " + err)
        if src == "audit":
            return {}, "", "; ".join(errs), ""
    got, err = read_metric(api, ids, names)
    if not err:
        return got, "metric_instance", "", ("; ".join(errs) + " - the paths from metric_instance instead") if errs else ""
    errs.append("metric_instance: " + err)
    return {}, "", "; ".join(errs), ""


def read_members(api, groups, chunk=50):
    """The members of these groups (sys_user_grmember; groups: {sys_id: name}, or {name: name} when the ids are not
    known) -> ({group name: [names]}, error). Read with display values: a member is the name the records show."""
    out = dict((name, []) for name in groups.values())
    by_id = dict(groups)
    # by sys_id where it is known (group IN), by name where it is not (group.name IN)
    parts = [("group", sorted(k for k in groups if k != groups[k])), ("group.name", sorted(k for k in groups if k == groups[k]))]
    for field, keys in parts:
        for i in range(0, len(keys), chunk):
            part = keys[i:i + chunk]
            try:
                r = api.get("api/now/table/sys_user_grmember", {"sysparm_query": field + "IN" + ",".join(part), "sysparm_fields": "user,group",
                                                                 "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true",
                                                                 "sysparm_limit": 10000})
            except SnError as e:
                return {}, str(e)
            rows = r.get("result") if isinstance(r, dict) else None
            if not isinstance(rows, list):
                return {}, "GET api/now/table/sys_user_grmember: no result list in the answer"
            for x in rows:
                gv, gd = _vd(x, "group")
                name = by_id.get(gv) or by_id.get(gd) or gd
                user = " ".join(_vd(x, "user")[1].split())
                if name in out and user and user not in out[name]:
                    out[name].append(user)
    for name in out:
        out[name].sort()
    return out, ""


def read_work_notes(api, ids, per_id=20, chars=1500, chunk=100):
    """The work notes of these incidents (sys_journal_field, element work_notes; GET only) -> ({sys_id: [[when, text]]}
    newest first, at most per_id each, each cut to chars; error). Who wrote a note is not read."""
    out = {}
    ids = [str(x) for x in ids if str(x or "").strip()]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/sys_journal_field", {"sysparm_query": "element=work_notes^name=incident^element_idIN" + ",".join(part)
                                                            + "^ORDERBYDESCsys_created_on",
                                                            "sysparm_fields": "element_id,value,sys_created_on",
                                                            "sysparm_limit": min(10000, max(1, int(per_id)) * len(part))})
        except SnError as e:
            text = str(e)
            if "HTTP 403" in text or "HTTP 401" in text:
                text += " - the account needs read on sys_journal_field (the work notes of incidents)"
            return out, text
        rows = r.get("result") if isinstance(r, dict) else None
        if not isinstance(rows, list):
            return out, "GET api/now/table/sys_journal_field: no result list in the answer"
        for x in rows:
            k, text = str(x.get("element_id") or ""), str(x.get("value") or "").strip()
            if not k or not text or len(out.get(k, [])) >= int(per_id):
                continue
            out.setdefault(k, []).append([str(x.get("sys_created_on") or ""), text[:int(chars)]])
    for k in out:
        out[k].sort(key=lambda e: e[0], reverse=True)
    return out, ""


MANAGER_FIELDS = ",manager,manager.name,manager.email,manager.active"


def read_group_tree(api, ids, depth=6, chunk=100, managers=None):
    """The groups (sys_user_group, GET only) and their parents up the chain, at most depth levels, each read once (a
    cycle stops) -> ({sys_id: {name, parent, active, type}}, error). No person's field - but with managers (a dict,
    1.15.0) the same read asks each group's manager too (dot-walked: name, email, active), put in managers as
    {sys_id: {name, email, active}} with managers["_error"] when they could not be read (the groups are read without them)."""
    out, want, level = {}, [str(x) for x in ids if str(x or "").strip()], 0
    ask = managers is not None
    while want and level < int(depth):
        level += 1
        nxt = []
        for i in range(0, len(want), chunk):
            part = want[i:i + chunk]
            q = {"sysparm_query": "sys_idIN" + ",".join(part), "sysparm_fields": "sys_id,name,parent,active,type" + (MANAGER_FIELDS if ask else ""),
                 "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true", "sysparm_limit": len(part) + 10}
            try:
                r = api.get("api/now/table/sys_user_group", q)
            except SnError as e:
                if not ask:
                    return out, str(e)
                managers["_error"] = str(e)                # the groups again without the managers
                ask = False
                q["sysparm_fields"] = "sys_id,name,parent,active,type"
                try:
                    r = api.get("api/now/table/sys_user_group", q)
                except SnError as e2:
                    return out, str(e2)
            for x in (r.get("result") if isinstance(r, dict) else None) or []:
                k = str(_vd(x, "sys_id")[0] or "")
                if not k or k in out:
                    continue
                par = str(_vd(x, "parent")[0] or "")
                out[k] = {"name": " ".join(str(_vd(x, "name")[1] or "").split()), "parent": par, "active": _flag(_vd(x, "active")[0]) is not False,
                          "type": str(_vd(x, "type")[1] or "")}
                if ask and str(_vd(x, "manager")[0] or "").strip():
                    managers[k] = {"name": " ".join(str(_vd(x, "manager.name")[1] or "").split()), "email": str(_vd(x, "manager.email")[1] or "").strip(),
                                   "active": _flag(_vd(x, "manager.active")[0]) is not False, "id": str(_vd(x, "manager")[0])}
                if par and par not in out and par not in nxt:
                    nxt.append(par)
        want = [k for k in nxt if k not in out]
    return out, ""


def manager_names(mgr, gids, names):
    """The managers read (read_group_tree's) of the groups the records name -> ({group name: {name, email, active}}, error).
    A manager whose name came back empty: the account reads sys_user_group.manager but not the person (sys_user)."""
    if mgr.get("_error"):
        return {}, "%s - the groups were read without them" % mgr["_error"]
    out, hidden = {}, 0
    for gid in gids:
        m = mgr.get(gid)
        if not m:
            continue
        if not m["name"]:
            hidden += 1
            continue
        out[names.get(gid) or gid] = {"name": m["name"], "email": m["email"], "active": m["active"]}
    if hidden and not out:
        return {}, "the managers' names came back empty (%d groups): the account needs read on sys_user's name and email" % hidden
    return out, ""


def duration(text):
    """A ServiceNow duration's value ('1970-01-02 03:30:00' = 1 day 3.5 hours) -> seconds, or None."""
    m = re.match(r"^1970-01-(\d{2}) (\d{2}):(\d{2}):(\d{2})", str(text or "").strip())
    if not m:
        return None
    return (int(m.group(1)) - 1) * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))


def read_task_sla(api, ids, chunk=100, stats=None):
    """The SLAs of these tickets (task_sla, GET; cancelled left out) -> ({task sys_id: [{name, stage, breached, percent}]}, error).
    Breached: has_breached, or the stage breached; percent: business time where ServiceNow has it, else the clock's."""
    out = {}
    ids = [str(x) for x in ids if str(x or "").strip()]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/task_sla", {"sysparm_query": "taskIN" + ",".join(part) + "^stage!=cancelled",
                                                   "sysparm_fields": "task,sla,stage,has_breached,business_percentage,percentage,start_time,end_time,pause_time,"
                                                                     "business_time_left,planned_end_time",
                                                   "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true",
                                                   "sysparm_limit": 20 * len(part)})
        except SnError as e:
            text = str(e)
            if "HTTP 403" in text or "HTTP 401" in text:
                text += " - the account needs read on task_sla (the itil role has it)"
            return {}, text
        rows = r.get("result") if isinstance(r, dict) else None
        if not isinstance(rows, list):
            return {}, "GET api/now/table/task_sla: no result list in the answer"
        if stats is not None and len(rows) >= 20 * len(part):
            stats["more"] = True                         # a page full: some tickets' SLAs may be cut
        for x in rows:
            stage_v, stage_d = _vd(x, "stage")
            if stage_v.strip().lower() == "cancelled":
                continue
            pct = None
            for f in ("business_percentage", "percentage"):
                try:
                    pct = round(float(str(_vd(x, f)[0]).replace(",", "").strip()), 1)
                    break
                except ValueError:
                    continue
            out.setdefault(_vd(x, "task")[0], []).append({
                "name": " ".join(str(_vd(x, "sla")[1] or _vd(x, "sla")[0]).split()), "stage": stage_d or stage_v,
                "breached": _vd(x, "has_breached")[0].strip().lower() in ("true", "1") or stage_v.strip().lower() == "breached",
                "percent": pct, "paused_at": str(_vd(x, "pause_time")[0] or "") if stage_v.strip().lower() == "paused" else "",
                "running": stage_v.strip().lower() in ("in_progress", "paused"), "left_s": duration(_vd(x, "business_time_left")[0]),
                "planned_end": str(_vd(x, "planned_end_time")[0] or "")})
    return out, ""


def read_first_assigned(api, ids, chunk=100):
    """When each incident was first assigned to a person (metric_instance, the out-of-box metric "Assigned to"; GET) ->
    ({sys_id: UTC text}, error)."""
    out = {}
    ids = [str(x) for x in ids if str(x or "").strip()]
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/metric_instance", {"sysparm_query": "definition.name=Assigned to^table=incident^idIN" + ",".join(part)
                                                          + "^ORDERBYstart", "sysparm_fields": "id,value,start",
                                                          "sysparm_limit": 30 * len(part)})
        except SnError as e:
            return {}, str(e)
        for x in (r.get("result") if isinstance(r, dict) else None) or []:
            k, start = str(_vd(x, "id")[0] or ""), str(_vd(x, "start")[0] or "")
            if k and start and str(_vd(x, "value")[1] or _vd(x, "value")[0] or "").strip() and (k not in out or start < out[k]):
                out[k] = start
    return out, ""


def read_states(api, ids, name, chunk=100):
    """The incidents' state durations (metric_instance of the definition named; GET) -> ({sys_id: [[state as shown, start, end]]},
    error). 'none': the instance has no metric of that name (no row at all for these incidents)."""
    out = {}
    ids = [str(x) for x in ids if str(x or "").strip()]
    if not str(name or "").strip() or not ids:
        return out, ""
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            r = api.get("api/now/table/metric_instance", {"sysparm_query": "definition.name=%s^table=incident^idIN%s^ORDERBYstart" % (name, ",".join(part)),
                                                          "sysparm_fields": "id,value,start,end", "sysparm_display_value": "all",
                                                          "sysparm_exclude_reference_link": "true", "sysparm_limit": 50 * len(part)})
        except SnError as e:
            return {}, str(e)
        for x in (r.get("result") if isinstance(r, dict) else None) or []:
            k = str(_vd(x, "id")[0] or "")
            v = " ".join(str(_vd(x, "value")[1] or _vd(x, "value")[0] or "").split())
            if k and v:
                out.setdefault(k, []).append([v, str(_vd(x, "start")[0] or ""), str(_vd(x, "end")[0] or "")])
    for k in out:
        out[k].sort(key=lambda e: e[1])
    return out, ("" if out else "none")


SURVEY_FIELDS = ("instance.trigger_id,metric,metric.name,metric.datatype,metric.min,metric.max,metric.template,metric.metric_type,"
                 "metric.metric_type.name,metric.metric_type.state,metric.metric_type.active,actual_value,instance.taken_on")


def read_survey(api, win, metrics=None, limit=20000, types=None):
    """The incident surveys' answers in the window and the one before (asmt_metric_result of assessments triggered by
    incidents; GET; best-effort) -> ([[incident sys_id, question key, value, taken]], {question key: {name, survey, state,
    datatype, min, max, template}}, error). Only answers that are numbers. 1.16.0: each question (asmt_metric) with its survey
    (asmt_metric_type: name, state / active) and what ServiceNow says of its scale (datatype, min, max; a template's values),
    dot-walked in the same read - so two surveys' "Q1" stay apart. types: only those surveys, by name."""
    q = "instance.trigger_table=incident^instance.taken_on>=%s^instance.taken_on<=%s" % (win["prev_since"], win["until"])
    want = [str(m).strip() for m in metrics or [] if str(m).strip()]
    if want:
        q += "^metric.nameIN" + ",".join(want)
    kinds = [str(m).strip() for m in types or [] if str(m).strip()]
    if kinds:
        q += "^metric.metric_type.nameIN" + ",".join(kinds)
    try:
        r = api.get("api/now/table/asmt_metric_result", {"sysparm_query": q, "sysparm_fields": SURVEY_FIELDS,
                                                         "sysparm_display_value": "all", "sysparm_exclude_reference_link": "true", "sysparm_limit": limit})
    except SnError as e:
        return [], {}, str(e)
    rows = r.get("result") if isinstance(r, dict) else None
    if not isinstance(rows, list):
        return [], {}, "GET api/now/table/asmt_metric_result: no result list in the answer"
    out, qs = [], {}
    for x in rows:
        try:
            val = float(str(_vd(x, "actual_value")[0]).strip())
        except ValueError:
            continue
        inc, taken = str(_vd(x, "instance.trigger_id")[0] or ""), str(_vd(x, "instance.taken_on")[0] or "")
        if not (inc and taken):
            continue
        mid, mname = _vd(x, "metric")
        name = " ".join(str(_vd(x, "metric.name")[1] or _vd(x, "metric.name")[0] or mname or mid or "").split())
        key = str(mid or name)
        if key not in qs:
            st = (str(_vd(x, "metric.metric_type.state")[0] or "") + " " + str(_vd(x, "metric.metric_type.state")[1] or "")).lower()
            act = str(_vd(x, "metric.metric_type.active")[0] or "").lower()
            qs[key] = {"name": name, "survey": " ".join(str(_vd(x, "metric.metric_type.name")[1] or _vd(x, "metric.metric_type.name")[0]
                                                           or _vd(x, "metric.metric_type")[1] or "").split()),
                       "state": "retired" if ("retired" in st or act == "false") else ("active" if (st.strip() or act) else ""),
                       "datatype": str(_vd(x, "metric.datatype")[0] or "").strip().lower(),
                       "min": _num(_vd(x, "metric.min")[0]), "max": _num(_vd(x, "metric.max")[0]),
                       "template": str(_vd(x, "metric.template")[0] or "")}
        out.append([inc, key, val, taken])
    # a template's (likert) scale: its definitions' values - best-effort, one GET
    tids = sorted(set(v["template"] for v in qs.values() if v["datatype"] == "template" and re.match(r"^[\w-]{1,64}$", v["template"])))
    if tids:
        try:
            t = api.get("api/now/table/asmt_template_definition", {"sysparm_query": "templateIN" + ",".join(tids), "sysparm_fields": "template,value",
                                                                   "sysparm_exclude_reference_link": "true", "sysparm_limit": 1000})
            vals = {}
            for d in (t or {}).get("result") or []:
                v = _num(_vd(d, "value")[0])
                if v is not None:
                    vals.setdefault(str(_vd(d, "template")[0]), []).append(v)
            for v in qs.values():
                if vals.get(v["template"]):
                    v["min"], v["max"] = min(vals[v["template"]]), max(vals[v["template"]])
        except SnError:
            pass
    return out, qs, ""


def _num(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def read_slas(api, items, win, source="auto", first_assigned=False, max_records=20000, store=None):
    """Met or missed (1.7.0): task_sla for the incidents and requested items done in the window and the one before (auto,
    task_sla), else the report's targets; the metric Assigned to with the targets when asked -> (sla, first, first_error)."""
    asked = str(source or "auto").strip().lower()
    sla = {"source": asked if asked in ("targets", "made_sla") else "task_sla", "asked": asked, "error": "", "note": "", "records": {}}
    since, until = sn_time(win["prev_since"]), sn_time(win["until"])
    done = [it for it in items if it["table"] in ("incident", "sc_req_item") and sn_time(it.get("done_at"))
            and since <= sn_time(it["done_at"]) <= until]
    if sla["source"] == "task_sla":
        ids = [it["sys_id"] for it in done]
        sla["records"], sla["error"] = store.get("sla", ids, "task_sla", lambda xs: read_task_sla(api, xs)) if store is not None else read_task_sla(api, ids)
        sla["records"] = by_key(sla["records"])
        if sla["error"]:
            sla["source"] = "targets"
            sla["note"] = "task_sla not read (%s): the report's targets instead" % sla["error"]
    # the incidents open now (1.7.0): their SLAs running - at risk, breached and still open - and when an on-hold one's
    # was paused ("on hold since" where the state metric is not read)
    opened = sorted([it for it in items if it["table"] == "incident" and not sn_time(it.get("done_at")) and it.get("active") is not False],
                    key=lambda it: it.get("opened_at") or "")
    sla["paused"], sla["paused_error"], sla["open"], sla["open_cut"] = {}, "", {}, []
    if len(opened) > int(max_records):                  # capped as every read (max_records): the newest kept, said
        sla["open_cut"] = [int(max_records), len(opened)]
        opened = opened[-int(max_records):]
    if opened and not (sla["error"] and "HTTP 40" in sla["error"]):
        st = {}
        recs, sla["paused_error"] = read_task_sla(api, [it["sys_id"] for it in opened], stats=st)
        if st.get("more") and not sla["open_cut"]:
            sla["open_cut"] = [len(opened), len(opened)]
        sla["open"] = by_key(dict((k, [x for x in xs if x.get("running")]) for k, xs in recs.items()))
        for k, xs in recs.items():
            ts = sorted(x["paused_at"] for x in xs if x.get("paused_at"))
            if ts:
                sla["paused"][k] = ts[0]
    sla["paused"] = by_key(sla["paused"])
    first, ferr = {}, ""
    if sla["source"] == "targets" and first_assigned:
        ids = [it["sys_id"] for it in done if it["table"] == "incident"]
        first, ferr = store.get("first", ids, "assigned_to", lambda xs: read_first_assigned(api, xs)) if store is not None else read_first_assigned(api, ids)
    return sla, by_key(first), ferr


def sn_time(text):
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def keep(it, since, until):
    """In the report: opened before the window's end and not done before the window before began."""
    o, d = sn_time(it.get("opened_at")), sn_time(it.get("done_at"))
    if o is None or o > until:
        return False
    return d is None or d >= since


def is_open(it):
    """Still open for the ticket store (1.17.0): anything not inactive and done - its follow-up reads are made every run."""
    return not (it.get("active") is False and it.get("done_at"))


class Reuse(object):
    """The ticket store (1.17.0): what the database holds of each ticket, and what this run read. A ticket is fresh when
    the light read gives the same sys_updated_on the database holds; a fresh ticket's fields come from the database, and
    so do its follow-up reads (task_sla, the state metric, the hand-off paths, first assigned) once it is closed. rows:
    {sys_id: {u (sys_updated_on), c (STORE_CODE), i (the item less LIGHT_KEYS), f: {kind: {m (where it was read), v}}}}."""

    def __init__(self, rows):
        self.rows = rows or {}
        self.fresh, self.closed, self.upd, self.got = set(), set(), {}, {}

    def stored(self, sid, upd):
        r = self.rows.get(sid)
        return r if r and r.get("c") == STORE_CODE and upd and r.get("u") == upd and isinstance(r.get("i"), dict) else None

    def has(self, sid, kind, meta=None):
        f = ((self.rows.get(sid) or {}).get("f") or {}).get(kind) if sid in self.closed else None
        return isinstance(f, dict) and (meta is None or f.get("m") == meta)

    def note(self, kind, ids, meta, values):
        for i in ids:
            self.got.setdefault(i, {})[kind] = {"m": meta, "v": values.get(i)}

    def get(self, kind, ids, meta, reader, probe=True):
        """reader(ids) -> (values, error). The ids the database holds (closed, fresh, read the same way) are not asked
        again; one is, when none is left to ask (probe) - so this run still says whether the read works."""
        ids = list(ids)
        known = set(i for i in ids if self.has(i, kind, meta))
        ask = [i for i in ids if i not in known] or (ids[:1] if probe else [])
        got, err = reader(ask) if ask else ({}, "")
        if err:
            return got, err
        out = dict(got)
        for i in ids:
            if i in known and i not in out and i not in ask:
                v = self.rows[i]["f"][kind].get("v")
                if v is not None:
                    out[i] = v
        self.note(kind, ask, meta, got)
        return out, err


def stored_items(api, table, rows, store, chunk=100):
    """The light read's records -> the items, in the same order and the same as normalize gives for a full read (1.17.0):
    a fresh ticket from the database with this run's text, people and CI; the others read in full, by sys_id. A record
    gone between the two reads -> None (dropped)."""
    order = list(normalize({}, table))
    out, need = [], []
    for rec in rows:
        sid, upd = _vd(rec, "sys_id")[0], _vd(rec, "sys_updated_on")[0]
        st = store.stored(sid, upd)
        if st is None:
            need.append(sid)
            out.append(sid)
            continue
        it = dict(st["i"])
        it.update(light(rec, table))
        it = dict((k, it.get(k)) for k in order if k in it)
        store.fresh.add(sid)
        if not is_open(it):
            store.closed.add(sid)
        out.append(it)
    full = {}
    for i in range(0, len(need), chunk):
        part = [x for x in need[i:i + chunk] if re.match(r"^[\w-]{1,64}$", x)]
        if not part:
            continue
        got, _more = read_table(api, table, "sys_idIN" + ",".join(part), len(part))
        for rec in got:
            full[_vd(rec, "sys_id")[0]] = rec
    return [normalize(full[x], table) if isinstance(x, str) and x in full else (None if isinstance(x, str) else x) for x in out]


def stored_paths(api, ids, names, source, store):
    """The hand-off paths with the ticket store (1.17.0): the incidents not held asked first (or one, when all are held),
    which says where the paths come from this run; the held ones read that way reused, the rest asked that way. 1.17.0b:
    kept as sys_audit gives them (the groups' sys_ids) and named with this run's group names, so a renamed group shows
    its name now, as a full read shows it."""
    probe = [i for i in ids if not store.has(i, "paths")] or ids[:1]
    got, src, err, note = read_paths(api, probe, {}, source)
    if not src:
        return got, src, err, note
    forced = "audit" if src == "sys_audit" else "metric"
    rest = [i for i in ids if i not in set(probe)]
    more, err2 = store.get("paths", rest, src, lambda xs: (lambda r: (r[0], r[2]))(read_paths(api, xs, {}, forced)), probe=False)
    if err2:
        return {}, "", err2, ""
    store.note("paths", probe, src, got)
    out = dict(got)
    out.update(more)
    if src == "sys_audit":
        out = dict((k, [[e[0], names.get(e[1], e[1]), names.get(e[2], e[2])] for e in v]) for k, v in out.items())
    return out, src, err, note


def by_key(d):
    """A follow-up read's answer in sys_id order: the same for a run that reads everything and one that reuses the store."""
    return dict(sorted((d or {}).items()))


def read_all(api, groups, tables, win, max_records=20000, audit=True, handoff_min=3, members=True, group_parents=True, handoff_source="auto",
             sla_source="auto", first_assigned=False, state_metric="Incident State Duration", survey=True, survey_metrics=None, managers=False, survey_types=None,
             store=None):
    """store (1.17.0): None - every record read in full (and nothing kept); a Reuse - its tickets reused where fresh
    (store.rows empty: a full read that fills the store)."""
    since, until = sn_time(win["prev_since"]), sn_time(win["until"])
    inc = store is not None and bool(store.rows)
    res = {"ok": False, "error": "", "window": win, "groups": [], "tables": [], "items": [], "audit": {}, "audit_error": "",
           "members": {}, "members_error": "", "lookup_note": "", "url": api.url, "group_tree": {}, "group_tree_error": "",
           "audit_source": "", "audit_note": "", "managers": {}, "managers_error": ""}
    g, gerr = lookup_groups(api, groups)
    res["groups"] = g
    gids = [x["sys_id"] for x in g if x.get("sys_id")]
    gnames = []
    if gerr and groups:
        gnames = [x["name"] for x in g]
        res["lookup_note"] = "the assignment groups were not looked up (%s): matched by name instead" % gerr
    if groups and not gids and not gnames:
        res["error"] = "none of the assignment groups is in ServiceNow: %s" % ", ".join(x["name"] for x in g)
        return res
    seen, items = set(), []
    since_w = sn_time(win["since"])
    for t in [str(x).strip() for x in tables or [] if str(x).strip()]:
        entry = {"table": t, "read": 0, "kept": 0, "more": False, "error": ""}
        try:
            query = table_query(t, gids, gnames, since, until)
            fields = (LIGHT_COMMON + LIGHT_TABLE.get(t, [])) if inc else None
            rows, more = read_table(api, t, query, max_records, fields=fields)
            if more:
                # 1.16.0: cut at the oldest opened_at kept (newest first). The records opened before the cut that count in
                # the window itself (open, or done in it) are read as well: the window stays whole, the one before is cut.
                # That second read capped too: the window is cut, before its oldest record read.
                oldest = lambda rs: min([x for x in (sn_time(_vd(r_, "opened_at")[0]) for r_ in rs) if x] or [until])  # noqa: E731
                cut = oldest(rows)
                entry.update(cut=cut.strftime("%Y-%m-%d %H:%M:%S"), total=count_records(api, t, query))
                old_rows, old_more = read_table(api, t, older_query(t, gids, gnames, since_w, cut), max_records, fields=fields)
                had = set(_vd(r_, "sys_id")[0] for r_ in rows)
                rows += [r_ for r_ in old_rows if _vd(r_, "sys_id")[0] not in had]      # the cut's own second: read twice
                entry["older"] = len(old_rows)
                if old_more:
                    entry.update(window_cut=True, cut=oldest(old_rows).strftime("%Y-%m-%d %H:%M:%S"))
        except SnError as e:
            entry["error"] = str(e)
            res["tables"].append(entry)
            continue
        entry["read"], entry["more"] = len(rows), more
        try:
            made = stored_items(api, t, rows, store) if inc else [normalize(rec, t) for rec in rows]
        except SnError as e:
            entry["error"] = str(e)
            res["tables"].append(entry)
            continue
        if store is not None:
            for rec in rows:
                store.upd[_vd(rec, "sys_id")[0]] = _vd(rec, "sys_updated_on")[0]
        for it in made:
            if it is None or not it["sys_id"] or it["sys_id"] in seen or not keep(it, since, until):
                continue
            seen.add(it["sys_id"])
            items.append(it)
            entry["kept"] += 1
        res["tables"].append(entry)
    res["items"] = items
    # 1.16.0: any cut drops the compare (the deltas, said). A cut in the window before only: the window's figures whole; a cut
    # in the window itself: said on every page, nothing written to the report database
    res["cut"] = [x for x in res["tables"] if x.get("window_cut")]
    if win.get("compare") and any(x.get("more") for x in res["tables"]):
        if not res["cut"]:
            res["compare_cut"] = [x["table"] for x in res["tables"] if x.get("more")]
        res["window"] = win = dict(win, prev_since=win["since"], compare=False, prev_label="")
    res["ok"] = any(not x["error"] for x in res["tables"])
    if not res["ok"]:
        res["error"] = ("no table could be read: " + "; ".join("%s - %s" % (x["table"], x["error"]) for x in res["tables"])) if res["tables"] \
            else "no table to read: set servicenow_insights_tables"
        return res
    names = {}
    for it in items:
        if it.get("group"):
            names[it.get("group_id") or it["group"]] = it["group"]
    if audit:
        ids = [it["sys_id"] for it in items if it["table"] == "incident" and it.get("reassignment_count", 0) >= int(handoff_min)]
        if ids and store is not None:
            res["audit"], res["audit_source"], res["audit_error"], res["audit_note"] = stored_paths(api, ids, names, handoff_source, store)
        elif ids:
            res["audit"], res["audit_source"], res["audit_error"], res["audit_note"] = read_paths(api, ids, names, handoff_source)
        res["audit"] = by_key(res["audit"])
    if members and names:
        res["members"], res["members_error"] = read_members(api, names)
    gids = sorted(set(it.get("group_id") for it in items if it.get("group_id") and re.match(r"^[\w-]{1,64}$", str(it["group_id"]))))
    mgr = {} if managers else None
    if group_parents and gids:
        res["group_tree"], res["group_tree_error"] = read_group_tree(api, gids, managers=mgr)
    elif managers and gids:                               # 1.15.0: the teams' own groups only, for their managers
        _tree, err = read_group_tree(api, gids, depth=1, managers=mgr)
        if err:
            mgr["_error"] = err
    if managers:
        res["managers"], res["managers_error"] = manager_names(mgr, gids, names)
    res["sla"], res["first_assigned"], res["first_assigned_error"] = read_slas(api, items, win, sla_source, first_assigned, max_records, store)
    # 1.7.0: the time on hold (the incidents on hold now, and those done in the window), the surveys
    hold_ids = [it["sys_id"] for it in items if it["table"] == "incident" and (
        ("hold" in str(it.get("state") or "").lower() and it.get("active") is not False) or (sn_time(it.get("done_at")) and sn_time(it["done_at"]) >= since_w))]
    if store is not None and str(state_metric or "").strip() and hold_ids:
        # 'none' (no row at all) is said of what this run holds, read or reused - as a full read says it
        got, err = store.get("states", hold_ids, state_metric, lambda xs: (lambda r: (r[0], "" if r[1] == "none" else r[1]))(read_states(api, xs, state_metric)))
        res["states"], res["states_error"] = (got, err) if err else (got, "" if got else "none")
    else:
        res["states"], res["states_error"] = read_states(api, hold_ids, state_metric)
    res["states"] = by_key(res["states"])
    res["survey"], res["survey_questions"], res["survey_error"] = read_survey(api, win, survey_metrics, types=survey_types) \
        if survey and "incident" in [str(t) for t in tables or []] else ([], {}, "")
    return res


def store_load(path, full_refresh=False):
    """The rows the database holds (site_insights_db tickets_load wrote them to path) -> (Reuse, why it is a full read or
    ''). Rows of another format are left out: those tickets are read again."""
    rows = {}
    try:
        with open(path) as f:
            got = json.load(f)
        rows = dict((k, v) for k, v in ((got or {}).get("rows") or {}).items() if isinstance(v, dict) and v.get("c") == STORE_CODE)
    except (IOError, OSError, ValueError):
        rows = {}
    if full_refresh:
        return Reuse({}), "servicenow_insights_full_refresh"
    return Reuse(rows), ("" if rows else "the database holds no tickets yet")


def store_save(res, store):
    """The rows to write after a run (1.17.0): every ticket read from ServiceNow this run, and every reused one whose
    follow-up reads changed - its fields less the text, the people and the CI (LIGHT_KEYS)."""
    out = []
    for it in res.get("items") or []:
        sid = it["sys_id"]
        old = store.rows.get(sid) if sid in store.fresh else None
        f = dict((old or {}).get("f") or {})
        f.update(store.got.get(sid) or {})
        if old is not None and f == (old.get("f") or {}):
            continue
        out.append({"s": sid, "t": it["table"], "n": it.get("number") or "", "u": store.upd.get(sid) or "", "a": it.get("active") is not False,
                    "o": it.get("opened_at") or "", "d": it.get("done_at") or "", "g": it.get("group_id") or "", "c": STORE_CODE,
                    "i": dict((k, v) for k, v in it.items() if k not in LIGHT_KEYS), "f": f})
    return out


def store_finish(res, store, why_full, out_path):
    """1.17.0: how this run read (res["store"], for the job's last line and How this was made), and the rows to write to
    out_path - unless the window itself was cut (servicenow_insights_max_records: nothing of it is kept) or nothing was
    read. A cut in the window before only keeps the tickets read: they are full reads. seen (1.17.0b): every ticket the
    light read gave, so tickets_save can prune the ones no longer there."""
    n = len(res.get("items") or [])
    db = sum(1 for it in res.get("items") or [] if it["sys_id"] in store.fresh)
    res["store"] = {"mode": "incremental" if not why_full else "full", "why": why_full, "from_sn": n - db, "from_db": db, "saved": 0,
                    "not_saved": ""}
    if not res.get("ok"):
        res["store"]["not_saved"] = "nothing read"
    elif res.get("cut"):
        res["store"]["not_saved"] = "the window's records were cut (servicenow_insights_max_records)"
    elif out_path:
        rows = store_save(res, store)
        with open(out_path, "w") as f:
            json.dump({"rows": rows, "seen": sorted(it["sys_id"] for it in res.get("items") or [])}, f)
        res["store"]["saved"] = len(rows)
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(
            url=dict(type="str", required=True), username=dict(type="str", required=True), password=dict(type="str", required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), ca_path=dict(type="path"), timeout=dict(type="int", default=30),
            groups=dict(type="list", elements="str", default=[]),
            tables=dict(type="list", elements="str", default=["incident", "sc_req_item", "sc_task", "change_request", "change_task", "problem"]),
            months=dict(type="int", default=6), days=dict(type="float", default=0), until=dict(type="str", default=""),
            compare=dict(type="bool", default=True), time_zone=dict(type="str", default=""),
            max_records=dict(type="int", default=20000), audit=dict(type="bool", default=True), handoff_min=dict(type="int", default=3),
            members=dict(type="bool", default=True), now=dict(type="str", default=""), group_parents=dict(type="bool", default=True),
            handoff_source=dict(type="str", default="auto", choices=["auto", "audit", "metric", "none"]),
            work_notes_for=dict(type="list", elements="str", default=[]), work_notes_max=dict(type="int", default=20),
            work_note_chars=dict(type="int", default=1500),
            sla_source=dict(type="str", default="auto", choices=["auto", "task_sla", "targets", "made_sla"]),
            first_assigned=dict(type="bool", default=False),
            state_metric=dict(type="str", default="Incident State Duration"), survey=dict(type="bool", default=True),
            survey_metrics=dict(type="list", elements="str", default=[]), managers=dict(type="bool", default=False),
            survey_types=dict(type="list", elements="str", default=[]),
            store_in=dict(type="path", default=""), store_out=dict(type="path", default=""), full_refresh=dict(type="bool", default=False),
        ),
        supports_check_mode=True)
    p = module.params
    if p["work_notes_for"]:          # only the work notes of these incidents (ACT's fixes, 0.96.0)
        url = p["url"].strip()
        if url and not url.startswith("http"):
            url = "https://" + url
        api = Api(url, p["username"], p["password"], p["validate_certs"], p["ca_path"], p["timeout"])
        notes, err = read_work_notes(api, p["work_notes_for"], p["work_notes_max"], p["work_note_chars"])
        module.exit_json(changed=False, ok=not err, work_notes=notes, work_notes_error=err, url=url)
    now = None
    if p["now"]:
        try:
            now = datetime.datetime.strptime(p["now"].replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            module.fail_json(msg="now must be YYYY-MM-DDTHH:MM:SS (UTC), not %s" % p["now"])
    try:
        win = window(p["months"], p["days"], p["until"], p["compare"], p["time_zone"], now)
    except ValueError as e:
        module.fail_json(msg="the window: %s" % e)
    url = p["url"].strip()
    if url and not url.startswith("http"):
        url = "https://" + url
    api = Api(url, p["username"], p["password"], p["validate_certs"], p["ca_path"], p["timeout"])
    store, why_full = store_load(p["store_in"], p["full_refresh"]) if p["store_in"] else (None, "")
    try:
        res = read_all(api, p["groups"], p["tables"], win, p["max_records"], p["audit"], p["handoff_min"], p["members"], p["group_parents"], p["handoff_source"],
                       p["sla_source"], p["first_assigned"], p["state_metric"], p["survey"], p["survey_metrics"], p["managers"], p["survey_types"],
                       store)
    except SnError as e:
        res = {"ok": False, "error": str(e), "window": win, "groups": [], "tables": [], "items": [], "audit": {}, "audit_error": "",
               "members": {}, "members_error": "", "lookup_note": "", "url": url}
    if store is not None:
        store_finish(res, store, why_full, p["store_out"])
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
