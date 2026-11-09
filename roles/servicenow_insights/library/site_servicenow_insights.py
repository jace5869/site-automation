#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Ticket insights (roles/servicenow_insights): months of ServiceNow records read for the report - incidents, requested
items, catalog tasks, changes, problems - in the window and the window before it, and anything still open; and the
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
  tables: {description: The tables read., type: list, elements: str, default: [incident, sc_req_item, sc_task, change_request, problem]}
  months: {description: The window - this many months up to until., type: int, default: 6}
  days: {description: "The window in days (> 0 wins over months).", type: float, default: 0}
  until: {description: "The window's last day (through its end); '' = now.", type: str, default: ''}
  compare: {description: Read the window before as well (the same length)., type: bool, default: true}
  time_zone: {description: "The zone of the dates given and of the window's whole days ('' = UTC).", type: str, default: ''}
  max_records: {description: At most this many records per table., type: int, default: 20000}
  audit: {description: Read the assignment history (sys_audit) of the incidents passed around., type: bool, default: true}
  handoff_min: {description: The reassignments that make an incident passed around., type: int, default: 3}
  members: {description: Read the groups' members (sys_user_grmember) for the leaderboards., type: bool, default: true}
  now: {description: "The clock (tests): YYYY-MM-DDTHH:MM:SS UTC.", type: str, default: ''}
  work_notes_for: {description: "Incident sys_ids: read only their work notes (sys_journal_field), nothing else.", type: list, elements: str, default: []}
  work_notes_max: {description: The newest work notes kept per incident., type: int, default: 20}
  work_note_chars: {description: Each work note cut to this many characters., type: int, default: 1500}
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
audit_error: {description: "Why sys_audit was not read ('' = read, or not needed).", type: str}
members: {description: "{group name: [the members' names]} for every group the records name (sys_user_grmember).", type: dict}
members_error: {description: "Why sys_user_grmember was not read ('' = read, or not asked).", type: str}
url: {description: The instance's address., type: str}
work_notes: {description: "With work_notes_for: {incident sys_id: [[when (UTC text), text], ...]}, newest first.", type: dict}
work_notes_error: {description: "Why sys_journal_field was not read ('' = read).", type: str}
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
          "closed_by", "close_notes", "active", "cmdb_ci", "opened_by", "made_sla", "reassignment_count", "sys_created_on"]
TABLE_FIELDS = {
    "incident": ["resolved_at", "resolved_by", "close_code", "category", "subcategory", "caller_id", "reopen_count", "caused_by",
                 "problem_id"],
    "sc_req_item": ["cat_item", "requested_for"],
    "sc_task": ["request_item"],
    "change_request": ["type", "close_code", "start_date", "end_date", "work_start", "work_end", "category"],
    "problem": ["resolved_at", "category"],
}
# the tables whose records have a resolved_at besides closed_at
RESOLVES = ("incident", "problem")


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


def normalize(rec, table):
    """One record -> the item the report uses: only the fields it reads, people and states as shown, dates as UTC text."""
    d = lambda f: " ".join(_vd(rec, f)[1].split())          # noqa: E731
    v = lambda f: _vd(rec, f)[0]                            # noqa: E731
    it = {"table": table, "sys_id": v("sys_id"), "number": v("number") or d("number"),
          "short_description": d("short_description"), "state": d("state"), "priority": d("priority"),
          "priority_value": _prio(v("priority") or d("priority")),
          "assigned_to": d("assigned_to"), "group": d("assignment_group"),
          "group_id": v("assignment_group"), "opened_by": d("opened_by"), "cmdb_ci": d("cmdb_ci"),
          "opened_at": v("opened_at") or v("sys_created_on"), "closed_at": v("closed_at"), "closed_by": d("closed_by"),
          "close_notes": d("close_notes"), "active": _flag(v("active")), "made_sla": _flag(v("made_sla")),
          "reassignment_count": _int(v("reassignment_count"))}
    if table in RESOLVES:
        it["resolved_at"] = v("resolved_at")
    if table == "incident":
        it.update({"resolved_by": d("resolved_by"), "close_code": d("close_code"), "category": d("category"),
                   "subcategory": d("subcategory"), "caller": d("caller_id"), "caller_id": v("caller_id"),
                   "reopen_count": _int(v("reopen_count")), "caused_by": d("caused_by"), "problem": d("problem_id")})
    elif table == "sc_req_item":
        it.update({"cat_item": d("cat_item"), "caller": d("requested_for")})
    elif table == "sc_task":
        it["parent"] = d("request_item")
    elif table == "change_request":
        it.update({"change_type": d("type"), "change_type_value": v("type").lower(), "close_code": d("close_code"),
                   "start_date": v("work_start") or v("start_date"), "end_date": v("work_end") or v("end_date"), "category": d("category")})
    elif table == "problem":
        it["category"] = d("category")
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
    return "^".join(q) + "^ORDERBYopened_at"


def read_table(api, table, query, max_records=20000, page=PAGE):
    fields = COMMON + TABLE_FIELDS.get(table, [])
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


def read_all(api, groups, tables, win, max_records=20000, audit=True, handoff_min=3, members=True):
    since, until = sn_time(win["prev_since"]), sn_time(win["until"])
    res = {"ok": False, "error": "", "window": win, "groups": [], "tables": [], "items": [], "audit": {}, "audit_error": "",
           "members": {}, "members_error": "", "lookup_note": "", "url": api.url}
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
    for t in [str(x).strip() for x in tables or [] if str(x).strip()]:
        entry = {"table": t, "read": 0, "kept": 0, "more": False, "error": ""}
        try:
            rows, more = read_table(api, t, table_query(t, gids, gnames, since, until), max_records)
        except SnError as e:
            entry["error"] = str(e)
            res["tables"].append(entry)
            continue
        entry["read"], entry["more"] = len(rows), more
        for rec in rows:
            it = normalize(rec, t)
            if not it["sys_id"] or it["sys_id"] in seen or not keep(it, since, until):
                continue
            seen.add(it["sys_id"])
            items.append(it)
            entry["kept"] += 1
        res["tables"].append(entry)
    res["items"] = items
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
        if ids:
            res["audit"], res["audit_error"] = read_audit(api, ids, names)
    if members and names:
        res["members"], res["members_error"] = read_members(api, names)
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(
            url=dict(type="str", required=True), username=dict(type="str", required=True), password=dict(type="str", required=True, no_log=True),
            validate_certs=dict(type="bool", default=True), ca_path=dict(type="path"), timeout=dict(type="int", default=30),
            groups=dict(type="list", elements="str", default=[]),
            tables=dict(type="list", elements="str", default=["incident", "sc_req_item", "sc_task", "change_request", "problem"]),
            months=dict(type="int", default=6), days=dict(type="float", default=0), until=dict(type="str", default=""),
            compare=dict(type="bool", default=True), time_zone=dict(type="str", default=""),
            max_records=dict(type="int", default=20000), audit=dict(type="bool", default=True), handoff_min=dict(type="int", default=3),
            members=dict(type="bool", default=True), now=dict(type="str", default=""),
            work_notes_for=dict(type="list", elements="str", default=[]), work_notes_max=dict(type="int", default=20),
            work_note_chars=dict(type="int", default=1500),
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
    try:
        res = read_all(api, p["groups"], p["tables"], win, p["max_records"], p["audit"], p["handoff_min"], p["members"])
    except SnError as e:
        res = {"ok": False, "error": str(e), "window": win, "groups": [], "tables": [], "items": [], "audit": {}, "audit_error": "",
               "members": {}, "members_error": "", "lookup_note": "", "url": url}
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
