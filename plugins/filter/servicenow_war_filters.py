# -*- coding: utf-8 -*-
"""The weekly activity report (playbooks/servicenow_war.yml, roles/servicenow_war): what site_servicenow_war read - everything
an assignment group opened, completed (with the date), still has open and worked on in the week - as one report
(roles/site_email's _report), the workbook for Excel, the lines to paste into the WAR, and the evidence and the question
for ACT (GenAI) to write the narrative."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:            # Python 3.8: UTC only
    ZoneInfo = None

UTC = datetime.timezone.utc
RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
MARK_BEGIN, MARK_END = "BEGIN_ACT_ANALYSIS", "END_ACT_ANALYSIS"
CORE = ["Number", "Type", "Short description", "State", "Priority", "Assigned to"]


def _tz(name):
    n = str(name or "").strip()
    if not n or n.upper() == "UTC" or ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(n)
    except Exception:            # noqa: BLE001
        return UTC


def _dt(text):
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def when_local(text, tz=None, fmt="%Y-%m-%d %H:%M"):
    """A UTC 'YYYY-MM-DD HH:MM:SS' from ServiceNow as the report shows it: in the report's time zone, minutes."""
    d = _dt(text)
    if d is None:
        return ""
    return d.astimezone(tz or UTC).strftime(fmt)


def day_local(text, tz=None):
    return when_local(text, tz, "%Y-%m-%d")


def _short(text, n):
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    return t[:n] if n <= 3 else t[:n - 3].rstrip() + "..."


def _days(v):
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if f >= 10 or f == int(f) else "%.1f" % f


def _who(it):
    return it.get("assigned_to") or "(unassigned)"


def _n(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _week_word(it):
    return "new" if it.get("opened") else ("worked on" if it.get("touched") else "untouched")


def _done_word(it):
    return "resolved" if it.get("done_how") == "resolved" else "closed"


def _cut(rows, max_rows):
    """(rows kept, note) - at most max_rows, said."""
    if max_rows and len(rows) > max_rows:
        return rows[:max_rows], " The first %d of %d are shown; the workbook and the artifacts (servicenow_war) have all." % (max_rows, len(rows))
    return rows, ""


def _sorted_items(r):
    return list((r or {}).get("items") or [])


def war_sets(r, old_days=30, open_scope="worked"):
    """The week's sets from the module's result -> dict(items, done, cancelled, opened, open_end, open_listed, touched, old,
    open_scope). open_listed = the open tasks the report lists: with open_scope worked (the default) the ones new this week
    or updated in it - the untouched backlog is counted, not listed; all = every task open at the week's end."""
    items = _sorted_items(r)
    scope = "all" if str(open_scope or "worked").strip().lower() == "all" else "worked"
    done = sorted([x for x in items if x.get("done")], key=lambda x: (x.get("done_at") or "", x.get("number") or ""))
    cancelled = sorted([x for x in items if x.get("cancelled")], key=lambda x: (x.get("done_at") or "", x.get("number") or ""))
    opened = sorted([x for x in items if x.get("opened")], key=lambda x: (x.get("opened_at") or "", x.get("number") or ""))
    open_end = sorted([x for x in items if x.get("open_at_end")], key=lambda x: (x.get("opened_at") or "", x.get("number") or ""))
    touched = [x for x in items if x.get("touched")]
    old = [x for x in open_end if (x.get("age_days") or 0) > float(old_days)]
    listed = open_end if scope == "all" else [x for x in open_end if x.get("opened") or x.get("touched")]
    return {"items": items, "done": done, "cancelled": cancelled, "opened": opened, "open_end": open_end, "open_listed": listed,
            "touched": touched, "old": old, "open_scope": scope}


def _sla_word(x):
    """' - SLA breached' / ' - SLA at 82%' for a line, or ''."""
    if x.get("sla_state") == "breached":
        return " - SLA breached"
    if x.get("sla_state") == "at_risk":
        return " - SLA at %s%%" % _num(x.get("sla_percent"))
    return ""


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    return str(int(f)) if f.is_integer() else "%.1f" % f


def servicenow_war_lines(r, opts=None):
    """The lines to paste into the WAR -> {completed: [...], in_progress: [...]}: one per task, its number, what it was,
    when it was resolved or closed (or how long it has been open) and who had it."""
    o = opts or {}
    tz = _tz(((r or {}).get("window") or {}).get("time_zone") or o.get("time_zone"))
    s = war_sets(r, o.get("old_days") or 30, o.get("open_scope"))
    done = ["%s %s: %s - %s %s (%s)" % (x.get("type") or "Task", x.get("number"), x.get("short_description") or "(no description)",
                                        _done_word(x), day_local(x.get("done_at"), tz), x.get("assigned_to") or "unassigned")
            for x in s["done"]]
    prog = []
    for x in sorted(s["open_listed"], key=lambda x: (-(x.get("age_days") or 0), x.get("number") or "")):
        age = x.get("age_days") or 0
        since = ("new this week" if x.get("opened") else "open since %s, %s days" % (day_local(x.get("opened_at"), tz), _days(age)))
        prog.append("%s %s: %s - %s, %s (%s)" % (x.get("type") or "Task", x.get("number"), x.get("short_description") or "(no description)",
                                                x.get("state") or "open", since, x.get("assigned_to") or "unassigned"))
    return {"completed": done, "in_progress": prog}


def servicenow_war_report(r, opts=None):
    """site_servicenow_war's result -> roles/site_email's _report. opts: title, old_days, max_rows, notes_chars, time_zone,
    act (ACT's version of the WAR follows the one-line-per-task version: that one says so). The order: the results, the
    WAR from the tables, ACT's version (servicenow_war_add_act), the SLAs, what was not read and read."""
    o = opts or {}
    r = r or {}
    win = r.get("window") or {}
    tz = _tz(win.get("time_zone") or o.get("time_zone"))
    old_days = float(o.get("old_days") or 30)
    max_rows = int(o.get("max_rows") or 300)
    notes_chars = int(o.get("notes_chars") if o.get("notes_chars") is not None else 200)
    s = war_sets(r, old_days, o.get("open_scope"))
    items, done, opened, open_end, touched, old, cancelled, open_listed = (s[k] for k in ("items", "done", "opened", "open_end", "touched", "old",
                                                                                           "cancelled", "open_listed"))
    scope_all = s["open_scope"] == "all"
    groups = list(r.get("groups") or [])
    users = list(r.get("users") or [])
    glabel = ", ".join(g.get("name", "") for g in groups) or ", ".join(u.get("name", "") for u in users) or "no group"
    missing_groups = [g["name"] for g in groups if g.get("found") is False]
    missing_users = [u["name"] for u in users if u.get("found") is False]
    tables = list(r.get("tables") or [])
    bad_tables = [t for t in tables if t.get("error")]
    cut_tables = [t for t in tables if t.get("more")]
    label = win.get("label") or ""
    ok = bool(r.get("ok"))
    sla = r.get("sla") or {}
    warn_pct = float(sla.get("warn_percent") or 75)
    sla_bad = [x for x in items if x.get("sla_state") == "breached"]
    sla_near = [x for x in items if x.get("sla_state") == "at_risk"]

    status = "info"
    if not ok:
        status = "critical"
        head = "not run - %s" % (r.get("error") or "ServiceNow could not be read")
    else:
        head = "%d completed, %d opened, %d open at the end (%s)" % (len(done), len(opened), len(open_end), label)
        if missing_groups or missing_users or bad_tables or cut_tables:
            status = "warning"
    report = {
        "title": "%s%s: %s" % (o.get("title") or "Weekly activity report", (" for " + glabel) if groups or users else "", head),
        "subtitle": " \u00b7 ".join(x for x in (("Assignment group: " if len(groups) == 1 else "Assignment groups: ") + glabel if groups else
                                                ("Assigned to: " + glabel if users else ""),
                                                "The week: %s%s" % (label, (" (%s)" % win["note"]) if win.get("note") else ""),
                                                ("ServiceNow: %s%s" % (r["url"], (" as " + r["user"]) if r.get("user") else "")) if r.get("url") else "") if x),
        "status": status,
        "summary": [{"label": "Completed", "value": len(done), "status": "ok" if done else "", "note": "resolved or closed"},
                    {"label": "Opened", "value": len(opened), "note": "new this week"},
                    {"label": "Open at the end", "value": len(open_end), "note": "still to do"},
                    {"label": "Older than %d days" % int(old_days), "value": len(old), "status": "warning" if old else "ok",
                     "note": "open the longest"},
                    {"label": "Worked on, still open", "value": len(touched), "note": "updated this week"},
                    {"label": "Cancelled", "value": len(cancelled), "note": "not counted as completed"}]
                   + ([{"label": "Not read", "value": len(bad_tables), "status": "critical"}] if bad_tables else []),
        "sections": [],
        "footer": "ServiceNow at %s. The week: %s. A task counts for the assignment group it has now: one moved to another group "
                  "since is not here. Completed = resolved (an incident, a problem) or closed in the week, on the record's own "
                  "timestamps; cancelled, skipped or closed incomplete is not completed (servicenow_war_cancelled_states); open at "
                  "the end = opened before the week's end and not completed by then. Dates in %s."
                  % (r.get("url") or "the credential's URL", label, win.get("time_zone") or "UTC"),
    }
    secs = report["sections"]
    if not ok:
        secs.append({"title": "Not run", "status": "critical", "lines": [r.get("error") or "ServiceNow could not be read"]
                     + (["%s: %s" % (t["table"], t["error"]) for t in bad_tables])})
        return report

    # 1. the week in numbers, per type
    by_type = {}
    for x in items:
        t = by_type.setdefault(x.get("type") or "Task", {"opened": 0, "done": 0, "cancelled": 0, "open_end": 0, "old": 0, "touched": 0})
        t["opened"] += bool(x.get("opened"))
        t["done"] += bool(x.get("done"))
        t["cancelled"] += bool(x.get("cancelled"))
        t["open_end"] += bool(x.get("open_at_end"))
        t["old"] += bool(x.get("open_at_end") and (x.get("age_days") or 0) > old_days)
        t["touched"] += bool(x.get("touched"))
    rows = [[k, v["opened"], v["done"], v["cancelled"], v["open_end"], v["old"], v["touched"]]
            for k, v in sorted(by_type.items(), key=lambda kv: (-kv[1]["done"], -kv[1]["open_end"], kv[0]))]
    if len(rows) > 1:
        rows.append(["Total", len(opened), len(done), len(cancelled), len(open_end), len(old), len(touched)])
    secs.append({"title": "The week in numbers", "columns": ["Type", "Opened", "Completed", "Cancelled", "Open at the end", "Older than %d days" % int(old_days),
                                                             "Worked on, still open"],
                 "rows": rows, "text": "Per kind of task. Opened and completed in the week; cancelled in it (not completed); open at the end of it, and of "
                                       "those the ones older than %d days; worked on = updated in the week, not new, still open." % int(old_days)})
    tail = []                                       # after the WAR and ACT's version of it: the SLAs, what was not read, read
    if sla.get("read") and items:
        sla_rows = (sorted([x for x in sla_bad if x.get("open_at_end")], key=lambda x: x.get("sla_breach_at") or "9")
                    + sorted(sla_near, key=lambda x: -(x.get("sla_percent") or 0))
                    + sorted([x for x in sla_bad if not x.get("open_at_end")], key=lambda x: x.get("sla_breach_at") or "9"))
        if sla_rows:
            tail.append({"title": "SLA breached or close to breaching", "status": "critical" if sla_bad else "warning",
                         "text": "From the tasks' SLAs in ServiceNow (task_sla). Breached: an SLA ran past its time. Close to breaching: an SLA "
                                 "still running with %s%% of its time or more used (servicenow_war_sla_warn_percent) - business time where "
                                 "the SLA has a schedule. Open breaches first, then the closest to breaching, then the week's completed tasks "
                                 "that breached." % _num(warn_pct),
                         "columns": ["Number", "Type", "Short description", "Assigned to", "State", "SLA", "Time used", "Breach time", "Status"],
                         "rows": [[x.get("number"), x.get("type"), _short(x.get("short_description"), 80), _who(x), x.get("state") or "-",
                                   x.get("sla_name") or "-", ("%s%%" % _num(x.get("sla_percent"))) if x.get("sla_percent") is not None else "-",
                                   when_local(x.get("sla_breach_at"), tz) or "-",
                                   ("breached" if x.get("open_at_end") else "breached, completed") if x.get("sla_state") == "breached"
                                   else "close to breaching"] for x in sla_rows],
                         "row_status": ["critical" if x.get("sla_state") == "breached" else "warning" for x in sla_rows]})
        else:
            tail.append({"title": "SLA breached or close to breaching", "status": "ok",
                         "lines": ["None of the week's tasks breached an SLA, and none is at %s%% of an SLA's time or more." % _num(warn_pct)]})
    if not items:
        secs.append({"title": "Result", "lines": ["Nothing was opened, completed or open for %s in the week %s." % (glabel, label)]})

    # 2. completed
    rows, rs = [], []
    for x in done:
        rows.append([when_local(x.get("done_at"), tz), x.get("number"), x.get("type"), x.get("short_description"), _who(x), x.get("state"),
                     x.get("priority"), day_local(x.get("opened_at"), tz), _days(x.get("days_to_done")), _short(x.get("close_notes"), notes_chars)])
    rows, note = _cut(rows, max_rows)
    if rows:
        secs.append({"title": "Completed this week", "status": "ok", "columns": ["Completed", "Number", "Type", "Short description", "Assigned to", "State",
                                                                                 "Priority", "Opened", "Days open", "Close notes"],
                     "rows": rows, "text": "Resolved or closed in the week, oldest first (an incident counts when it is resolved; its closure "
                                           "follows by itself later). The date of closure is the Completed column.%s" % note})
    # 3. opened
    rows = [[when_local(x.get("opened_at"), tz), x.get("number"), x.get("type"), x.get("short_description"), _who(x), x.get("state"), x.get("priority"),
             when_local(x.get("done_at"), tz) if x.get("done") else "-", x.get("opened_by")] for x in opened]
    rows, note = _cut(rows, max_rows)
    if rows:
        secs.append({"title": "Opened this week", "columns": ["Opened", "Number", "Type", "Short description", "Assigned to", "State", "Priority",
                                                              "Completed", "Opened by"],
                     "rows": rows, "text": "New in the week, oldest first. Completed: when it was already resolved or closed in the week.%s" % note})
    # 4. open at the end
    rows, rs = [], []
    for x in sorted(open_listed, key=lambda x: (-(x.get("age_days") or 0), x.get("number") or "")):
        age = x.get("age_days") or 0
        rows.append([day_local(x.get("opened_at"), tz), x.get("number"), x.get("type"), x.get("short_description"), _who(x), x.get("state"),
                     x.get("priority"), _days(age), when_local(x.get("updated_at"), tz), _week_word(x)])
        rs.append("warning" if age > old_days else "")
    rows, note = _cut(rows, max_rows)
    rest = len(open_end) - len(open_listed)
    backlog = ("%d more open task(s) were not touched this week - the backlog: counted in the tiles (Open at the end, Older than %d days) and on "
               "the workbook's Open at the end sheet, not listed here. servicenow_war_open_scope: all lists them." % (rest, int(old_days))) if rest else ""
    if rows:
        secs.append({"title": "Open at the end of the week", "status": "warning" if (old and scope_all) or any(rs) else "", "row_status": rs[:len(rows)],
                     "columns": ["Opened", "Number", "Type", "Short description", "Assigned to", "State", "Priority", "Days open", "Last update", "This week"],
                     "rows": rows, "text": "%s, the oldest first - amber over %d days. This week: new (opened in the week)%s.%s%s"
                                           % ("Still open at the week's end" if scope_all else "Open at the week's end and new this week or worked on in it",
                                              int(old_days), " or worked on (updated in it)" if not scope_all else ", worked on (updated in it) or untouched",
                                              note, (" " + backlog) if backlog else "")})
    elif rest:
        secs.append({"title": "Open at the end of the week", "status": "warning" if old else "",
                     "lines": ["No open task was new this week or worked on in it. " + backlog]})
    # 5. by person
    by_who = {}
    for x in items:
        p = by_who.setdefault(_who(x), {"done": 0, "opened": 0, "open_end": 0, "touched": 0, "cancelled": 0})
        p["done"] += bool(x.get("done"))
        p["opened"] += bool(x.get("opened"))
        p["open_end"] += bool(x.get("open_at_end"))
        p["touched"] += bool(x.get("touched"))
        p["cancelled"] += bool(x.get("cancelled"))
    rows = [[k, v["done"], v["opened"], v["open_end"], v["touched"], v["cancelled"]]
            for k, v in sorted(by_who.items(), key=lambda kv: (-kv[1]["done"], -kv[1]["open_end"], kv[0]))]
    if rows:
        secs.append({"title": "By person", "columns": ["Assigned to", "Completed", "Opened", "Open at the end", "Worked on, still open", "Cancelled"],
                     "rows": rows, "text": "Who had what, by the task's assignee now."})
    # 5b. cancelled: at the bottom, before what was not read - not the week's work
    if cancelled:
        rows = [[when_local(x.get("done_at"), tz), x.get("number"), x.get("type"), x.get("short_description"), _who(x), x.get("state"), x.get("priority"),
                 day_local(x.get("opened_at"), tz), _days(x.get("days_to_done")), _short(x.get("close_notes"), notes_chars)] for x in cancelled]
        rows, note = _cut(rows, max_rows)
        tail.append({"title": "Cancelled this week",
                     "columns": ["Cancelled", "Number", "Type", "Short description", "Assigned to", "State", "Priority", "Opened", "Days open", "Close notes"],
                     "rows": rows, "text": "Closed in the week as %s - not counted as completed, not in the WAR lines, not among ACT's accomplishments "
                                           "(servicenow_war_cancelled_states). Oldest first.%s"
                                           % (", ".join(sorted(set(str(x.get("state") or "cancelled") for x in cancelled))).lower(), note)})
    # 6. what was not read
    nr = ["%s: %s" % (t["table"], t["error"]) for t in bad_tables]
    nr += ["%s: more than the first %d records - raise servicenow_war_max_records, or narrow the week" % (t["table"], _n(t.get("read"))) for t in cut_tables]
    nr += ["assignment group %s is not in ServiceNow (its exact name, as the group list shows it)" % g for g in missing_groups]
    nr += ["user %s is not in ServiceNow (a user name or the full name)" % u for u in missing_users]
    if r.get("lookup_note"):
        nr.append(str(r["lookup_note"]))
    if sla.get("error"):
        nr.append("task_sla (the SLAs): %s - no SLA is shown; the API account needs read on task_sla (the itil role has it), "
                  "or set servicenow_war_sla: false" % sla["error"])
    if nr:
        tail.append({"title": "Not read", "status": "warning", "lines": nr,
                     "text": "A table the API account cannot read needs the role for it (itil reads incidents, catalog tasks and changes; "
                             "problem tasks need problem_read or itil on the problem tables), or take it out of servicenow_war_tables."})
    tail.append({"title": "Read", "lines": ["%s: %d record(s) kept of %d read" % (t["table"], _n(t.get("rows")), _n(t.get("read"))) for t in tables if not t.get("error")]})
    # 7. ready to paste: this version from the tables, then ACT's (servicenow_war_add_act puts it here), then the tail
    lines = servicenow_war_lines(r, {"old_days": old_days})
    both = ("Two versions of the WAR, to choose from: this one, one line per task from the tables, and ACT's, written by GenAI "
            "from the same tables, right after it. " if o.get("act") else "")
    war = []
    if lines["completed"]:
        war.append({"title": "For the WAR (from the tables): completed", "lines": lines["completed"],
                    "text": "One line per task, ready to paste: what it was, when it was resolved or closed, who had it."})
    if lines["in_progress"]:
        war.append({"title": "For the WAR (from the tables): in progress", "lines": lines["in_progress"],
                    "text": "Still open at the end of the week: its state, how long it has been open, who has it."})
    if war:
        war[0]["text"] = both + war[0]["text"]
    secs.extend(war)
    for t in tail:
        t["after_act"] = True                       # servicenow_war_add_act puts ACT's version before these
    secs.extend(tail)
    return report


def servicenow_war_add_act(report, act_sections):
    """The report with ACT's version of the WAR right after the version from the tables - before the SLAs and what was
    read (the sections marked after_act)."""
    rep = dict(report or {})
    secs = list(rep.get("sections") or [])
    i = next((n for n, x in enumerate(secs) if x.get("after_act")), len(secs))
    rep["sections"] = secs[:i] + list(act_sections or []) + secs[i:]
    return rep


def servicenow_war_xlsx(r, opts=None):
    """The workbook (roles/site_email's _report_xlsx): every item with every column on one sheet, then the week's sets and
    the counts, for sorting and pivoting in Excel."""
    o = opts or {}
    r = r or {}
    win = r.get("window") or {}
    tz = _tz(win.get("time_zone") or o.get("time_zone"))
    s = war_sets(r, o.get("old_days") or 30, "all")           # the workbook has everything, the backlog too
    extra = sorted(set(k for x in s["items"] for k in (x.get("extra") or {}).keys()))
    cols = ["Number", "Type", "Short description", "State", "Priority", "Assigned to", "Assignment group", "Opened by", "Configuration item",
            "Category", "Parent", "Opened", "Completed", "How", "Resolved", "Closed", "Last update", "Days open", "Days to complete",
            "Opened this week", "Completed this week", "Cancelled this week", "Open at the end", "Worked on", "SLA", "SLA status", "SLA time used %",
            "SLA breach time", "Close code", "Close notes", "Link"] + extra

    def yn(v):
        return "yes" if v else "no"

    def full(x):
        return [x.get("number"), x.get("type"), x.get("short_description"), x.get("state"), x.get("priority"), x.get("assigned_to"),
                x.get("assignment_group"), x.get("opened_by"), x.get("cmdb_ci"), x.get("category"), x.get("parent"),
                when_local(x.get("opened_at"), tz), when_local(x.get("done_at"), tz), x.get("done_how"), when_local(x.get("resolved_at"), tz),
                when_local(x.get("closed_at"), tz), when_local(x.get("updated_at"), tz), x.get("age_days"), x.get("days_to_done"),
                yn(x.get("opened")), yn(x.get("done")), yn(x.get("cancelled")), yn(x.get("open_at_end")), yn(x.get("touched")), x.get("sla_name") or "",
                {"breached": "breached", "at_risk": "close to breaching", "ok": "within"}.get(x.get("sla_state") or "", ""),
                x.get("sla_percent"), when_local(x.get("sla_breach_at"), tz), x.get("close_code"),
                x.get("close_notes"), x.get("link")] + [(x.get("extra") or {}).get(k, "") for k in extra]

    def core(x, when, when_label):
        return [x.get("number"), x.get("type"), x.get("short_description"), x.get("state"), x.get("priority"), _who(x), when_local(x.get(when), tz)]

    sheets = [{"name": "All items", "columns": cols, "rows": [full(x) for x in s["items"]]},
              {"name": "Completed", "columns": CORE + ["Completed", "Opened", "Days open", "Close notes"],
               "rows": [core(x, "done_at", "Completed") + [when_local(x.get("opened_at"), tz), x.get("days_to_done"), x.get("close_notes")] for x in s["done"]]},
              {"name": "Opened", "columns": CORE + ["Opened", "Completed"],
               "rows": [core(x, "opened_at", "Opened") + [when_local(x.get("done_at"), tz)] for x in s["opened"]]},
              {"name": "Open at the end", "columns": CORE + ["Opened", "Days open", "Last update", "This week"],
               "rows": [core(x, "opened_at", "Opened") + [x.get("age_days"), when_local(x.get("updated_at"), tz), _week_word(x)] for x in s["open_end"]]},
              {"name": "Cancelled", "columns": CORE + ["Cancelled", "Opened", "Days open", "Close notes"],
               "rows": [core(x, "done_at", "Cancelled") + [when_local(x.get("opened_at"), tz), x.get("days_to_done"), x.get("close_notes")] for x in s["cancelled"]]}]
    by_who, by_type = {}, {}
    for x in s["items"]:
        for key, d in ((_who(x), by_who), (x.get("type") or "Task", by_type)):
            p = d.setdefault(key, [0, 0, 0, 0, 0])
            p[0] += bool(x.get("done"))
            p[1] += bool(x.get("opened"))
            p[2] += bool(x.get("open_at_end"))
            p[3] += bool(x.get("touched"))
            p[4] += bool(x.get("cancelled"))
    sheets.append({"name": "By person", "columns": ["Assigned to", "Completed", "Opened", "Open at the end", "Worked on", "Cancelled"],
                   "rows": [[k] + v for k, v in sorted(by_who.items(), key=lambda kv: (-kv[1][0], kv[0]))]})
    sheets.append({"name": "By type", "columns": ["Type", "Completed", "Opened", "Open at the end", "Worked on", "Cancelled"],
                   "rows": [[k] + v for k, v in sorted(by_type.items(), key=lambda kv: (-kv[1][0], kv[0]))]})
    return sheets


def servicenow_war_name_list(value):
    """Assignment groups or people as a setting, a survey or a schedule gives them: a list as it is, or text - one name,
    or several on separate lines or separated by ; or , - never cut at spaces or dashes ('ABC - Ops - IT - Linux' is one
    group, 'Jane Doe' one person)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [w.strip() for w in re.split(r"[\n;,]+", value) if w.strip()]
    if isinstance(value, dict):
        raise ValueError("a list of names, not a mapping: %r" % (value,))
    return [str(w).strip() for w in value if str(w).strip()]


def servicenow_war_web_folder(names):
    """The website folder of one team's reports: its groups (or people) in lower case with dashes -
    'ABC - Ops - IT - Linux' -> 'abc-ops-it-linux'."""
    s = re.sub(r"[^a-z0-9]+", "-", "-".join(servicenow_war_name_list(names)).lower()).strip("-")
    return s[:80].rstrip("-") or "all"


# ---- ACT (GenAI) writes the narrative -----------------------------------------------------------------------

def servicenow_war_names(r):
    """The names ACT masks before anything reaches the model: the people, the groups, the configuration items."""
    out = set()
    for x in _sorted_items(r):
        for k in ("assigned_to", "opened_by", "assignment_group", "cmdb_ci"):
            if x.get(k):
                out.add(str(x[k]))
    for g in (r or {}).get("groups") or []:
        out.add(str(g.get("name") or ""))
    return sorted(n for n in out if n and n != "(unassigned)")


def servicenow_war_act_evidence(r, opts=None):
    """What ACT reads: the week, then every task of each set on one line."""
    o = opts or {}
    r = r or {}
    win = r.get("window") or {}
    tz = _tz(win.get("time_zone") or o.get("time_zone"))
    s = war_sets(r, o.get("old_days") or 30, o.get("open_scope"))
    max_items = int(o.get("max_items") or 150)
    notes = int(o.get("notes_chars") if o.get("notes_chars") is not None else 300)
    glabel = ", ".join(g.get("name", "") for g in r.get("groups") or []) or "the team"
    out = ["WEEK: %s" % (win.get("label") or ""), "ASSIGNMENT GROUP: %s" % glabel, ""]
    out.append("COMPLETED IN THE WEEK (%d):" % len(s["done"]))
    for x in s["done"][:max_items]:
        out.append("%s | %s | %s | %s %s | priority %s | %s | close notes: %s" % (
            x.get("number"), x.get("type"), x.get("short_description"), _done_word(x), day_local(x.get("done_at"), tz), x.get("priority") or "-",
            _who(x), _short(x.get("close_notes"), notes) or "-") + _sla_word(x).replace(" - ", " | ", 1))
    out += ["", "OPENED IN THE WEEK (%d):" % len(s["opened"])]
    for x in s["opened"][:max_items]:
        out.append("%s | %s | %s | opened %s | %s | priority %s | %s" % (
            x.get("number"), x.get("type"), x.get("short_description"), day_local(x.get("opened_at"), tz), x.get("state") or "-",
            x.get("priority") or "-", _who(x)))
    rest = len(s["open_end"]) - len(s["open_listed"])
    out += ["", "OPEN AT THE END OF THE WEEK (%d%s):" % (len(s["open_listed"]), " listed" if rest else "")]
    for x in sorted(s["open_listed"], key=lambda x: -(x.get("age_days") or 0))[:max_items]:
        out.append("%s | %s | %s | %s | open %s days | this week: %s | priority %s | %s" % (
            x.get("number"), x.get("type"), x.get("short_description"), x.get("state") or "-", _days(x.get("age_days") or 0), _week_word(x),
            x.get("priority") or "-", _who(x)) + _sla_word(x).replace(" - ", " | ", 1))
    if rest:
        oldest = max((x.get("age_days") or 0) for x in s["open_end"] if not (x.get("opened") or x.get("touched")))
        out.append("and %d more open task(s) not touched this week (the backlog; the oldest open %s days) - not listed, not this week's work" % (rest, _days(oldest)))
    if s["cancelled"]:
        out += ["", "CANCELLED IN THE WEEK (%d) - not accomplishments, not in progress:" % len(s["cancelled"])]
        for x in s["cancelled"][:max_items]:
            out.append("%s | %s | %s | %s %s | %s" % (x.get("number"), x.get("type"), x.get("short_description"), x.get("state") or "cancelled",
                                                    day_local(x.get("done_at"), tz), _who(x)))
    return "\n".join(out) + "\n"


def servicenow_war_act_task(opts=None):
    o = opts or {}
    return "\n".join([
        "Write the narrative of a weekly activity report (WAR) for an IT operations team, from the evidence piped to you: the team's "
        "ServiceNow tasks completed, opened and still open in the week. You cannot run any command in this task and must not try: no run "
        "action, no tool, no script to count words or check a figure - answer in ONE step, a finish action whose message is your whole "
        "answer. Estimate lengths; never count them.",
        "",
        "Give:",
        "- accomplishments: 3 to 8 bullet points of what the team completed this week. Group related tasks into one bullet (the same "
        "system, the same kind of work); name the ticket numbers in brackets; past tense; one sentence each; the most significant first;",
        "- in_progress: 2 to 6 bullets of the significant work still open - what it is, its state and how long it has been open; "
        "the oldest and the highest priorities first; ticket numbers in brackets;",
        "- issues: 0 to 4 bullets of what deserves attention: SLAs breached or close to breaching (the evidence says \"SLA breached\" "
        "or \"SLA at N%\"), old tasks, high priorities not moving, a pile-up of one kind, unassigned work. None: an empty list;",
        "- overall: two or three short sentences (at most 60 words) a manager reads first - the volume of the week in numbers, "
        "what stood out.",
        "Rules: use only the evidence; invent nothing; no cost, budget or procurement wording; keep people's names as the evidence "
        "has them. Each bullet is one plain sentence of about 30 words (your estimate: never count words with a command or a tool), one "
        "string in the list, the ticket numbers in brackets at "
        "its end - e.g. \"Patched and rebooted the six web hosts in the October window (CHG0012345, CTASK0012345)\". No markdown "
        "(no **, no #, no backticks), no bullet characters or numbering, no line breaks inside a string, no headings; the same "
        "work in one section only. Tasks under CANCELLED are not accomplishments and not in progress: name them only under issues, "
        "when the cancellations themselves deserve attention. The JSON must be valid: double quotes only, no comments, nothing "
        "after the END line.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"overall": "...", "items": [{"id": "accomplishments", "lines": ["...", "..."]}, {"id": "in_progress", "lines": ["..."]}, '
        '{"id": "issues", "lines": []}]}',
        MARK_END,
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def _act_clean(s):
    """One line of ACT's answer as plain text: no markdown, no leading bullet or number, one line."""
    s = str(s or "")
    s = re.sub(r"\*\*|__|`+", "", s)
    s = re.sub(r"^\s*(?:#+\s*|[-*\u2022\u00b7]\s+|\d{1,2}[.)]\s+)", "", s)
    return " ".join(s.split()).strip()


def _looks_like_war(v):
    return isinstance(v, dict) and (isinstance(v.get("items"), list) or any(k in v for k in ("accomplishments", "in_progress", "overall")))


def _json_variants(c):
    """A JSON text as the model may have mangled it: as it is; trailing commas gone; curly quotes straightened and //
    comments dropped. Each is read with strict=False (a raw line break inside a string is let through)."""
    yield c
    c2 = re.sub(r",\s*([}\]])", r"\1", c)
    yield c2
    c3 = re.sub(r"(?m)(^|[\]},])[ \t]*//[^\n]*$", r"\1", c2.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'"))
    yield re.sub(r",\s*([}\]])", r"\1", c3)


def _close_json(s):
    """A JSON text cut short (the model's output limit): the open string closed, a dangling key or comma dropped, the open
    brackets closed."""
    stack, in_str, esc, start = [], False, False, -1
    for n, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str, start = True, n
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    t = s[:start] if in_str else s                  # an unterminated string - the partial bullet, or a key - goes
    for _ in range(2):
        t = re.sub(r",\s*$", "", t.rstrip())
        t = re.sub(r'"[^"\n]*"\s*:\s*$', "", t.rstrip())      # a key with no value
        t = re.sub(r",\s*$", "", t.rstrip())
    return t.rstrip() + "".join(reversed(stack))


def _salvage(c):
    """The object out of a truncated answer: closed as it is, else cut back a comma at a time (the last, partial bullet is
    lost, the rest is read)."""
    t = c
    for _ in range(40):
        try:
            v = json.loads(_close_json(t), strict=False)
            if _looks_like_war(v):
                return v
        except ValueError:
            pass
        k = max(t.rfind(","), t.rfind("["), t.rfind("{"))
        if k <= 0:
            return None
        t = t[:k]
    return None


def _act_action(text):
    """ACT's answer is one of its own action records ({'command': ..., 'thought': ..., 'action': 'run'}): the model asked to
    run something - count the words of a bullet, check a figure - instead of answering, and evidence mode runs nothing.
    -> {action, command, thought}, or None."""
    t = str(text or "")
    m = re.search(r"""['"]action['"]\s*:\s*['"](run|edit|write|plan|ask|wait_job)['"]""", t)
    if not m:
        return None

    def grab(key):
        g = re.search(r"""['"]%s['"]\s*:\s*(['"])(.*?)\1\s*[,}]""" % key, t, re.S)
        return " ".join((g.group(2) if g else "").split())[:200]
    return {"action": m.group(1), "command": grab("command") or "(no command given)", "thought": grab("thought")}


PROSE_HEADS = (("accomplishments", r"accomplish|completed|achievement|done this week"), ("in_progress", r"in[ -]?progress|ongoing|still open|carried|open work"),
               ("issues", r"issue|attention|risk|concern|watch"), ("overall", r"overall|summary|executive|manager"))


def _prose(text):
    """ACT answered in words instead of JSON: its headings (Accomplishments, In progress, Issues / What deserves attention,
    Summary) and the lines under them, sorted into the sections. None when no section can be told apart."""
    if re.search(r'"\s*:\s*[\[{"]', str(text or "")):
        return None                                     # it is JSON the repairs could not read: shown as it came instead
    out = {"overall": "", "accomplishments": [], "in_progress": [], "issues": []}
    cur, free = None, []
    for raw in str(text or "").splitlines():
        raw = raw.strip()
        if not raw or raw in (MARK_BEGIN, MARK_END):
            continue
        head = re.sub(r"[\s:*#_`\-]+$", "", _act_clean(raw)).strip()
        marked = bool(re.match(r"^\s*(#+|\*\*|__)", raw)) or raw.endswith(":")
        key = next((k for k, pat in PROSE_HEADS if re.search(pat, head, re.I)), None) if head and len(head.split()) <= 5 else None
        if key and (marked or len(head.split()) <= 3):
            cur = key
            continue
        c = _act_clean(raw)
        if not c:
            continue
        if cur == "overall":
            out["overall"] = (out["overall"] + " " + c).strip()
        elif cur:
            out[cur].append(c)
        else:
            free.append(c)
    if not out["overall"] and free:
        out["overall"] = " ".join(free)[:400]
    return out if (out["accomplishments"] or out["in_progress"]) else None


def servicenow_war_act_parse(text):
    """ACT's answer -> {parsed, form, overall, accomplishments, in_progress, issues, error}: the JSON between the markers,
    else a ```json block, else the outermost {...} - read as it is or repaired (trailing commas, raw line breaks in strings,
    curly quotes, comments, an answer cut short); with no JSON at all, the prose sorted by its headings (form: prose)."""
    t = str(text or "")
    cands = []
    m = re.search(MARK_BEGIN + r"\s*(.*?)\s*" + MARK_END, t, re.S)
    if m:
        cands.append(m.group(1))
    elif MARK_BEGIN in t:
        cands.append(t.split(MARK_BEGIN, 1)[1])                       # the END marker never came: cut short
    cands += re.findall(r"```(?:json)?\s*(.*?)```", t, re.S)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    if i >= 0:
        cands.append(t[i:])
    obj = None
    for c in cands:
        c = c.strip()
        for attempt in _json_variants(c):
            try:
                v = json.loads(attempt, strict=False)
            except ValueError:
                continue
            if _looks_like_war(v):
                obj = v
                break
        if obj is not None:
            break
    cut = False
    if obj is None:
        for c in cands:
            obj = _salvage(list(_json_variants(c.strip()))[-1])          # straightened first: curly quotes would hide the strings
            if obj is not None:
                cut = True
                break
    if obj is None:
        act = _act_action(t)
        if act:
            return {"parsed": False, "form": "action", "overall": "", "accomplishments": [], "in_progress": [], "issues": [], "action": act,
                    "error": "ACT tried to run a command instead of answering (evidence mode runs none): %s" % act["command"]}
        prose = _prose(t)
        if prose is not None:
            return dict(prose, parsed=True, form="prose", error="")
        return {"parsed": False, "form": "", "overall": "", "accomplishments": [], "in_progress": [], "issues": [],
                "error": "ACT's answer has no readable JSON block" if t.strip() else "ACT gave no answer"}
    out = {"parsed": True, "form": "json-cut" if cut else "json", "overall": _act_clean(obj.get("overall") or obj.get("summary") or ""), "error": "",
           "accomplishments": [], "in_progress": [], "issues": []}

    def lines(v):
        if isinstance(v, (str, int, float)):
            v = [v]
        got = []
        for x in v or []:
            if isinstance(x, dict):                     # {"text": ...} or {"line": ...} for a bullet
                x = x.get("text") or x.get("line") or x.get("summary") or ""
            if not isinstance(x, (str, int, float)):
                continue
            for part in re.split(r"\n+|\s+(?=\u2022\s)", str(x)):     # one string holding several bullets
                c = _act_clean(part)
                if c and c.lower() not in (g.lower() for g in got):
                    got.append(c)
        return got

    for k in ("accomplishments", "in_progress", "issues"):
        if k in obj:
            out[k] = lines(obj.get(k))
    for it in obj.get("items") or []:
        if isinstance(it, dict):
            k = str(it.get("id") or "").strip().lower().replace(" ", "_").replace("-", "_")
            if k in out and isinstance(out[k], list):
                out[k] = lines(it.get("lines") if "lines" in it else it.get("text") or it.get("bullets"))
    return out


def servicenow_war_act_section(raw, opts=None):
    """ACT's version of the WAR as report sections (servicenow_war_add_act puts them right after the version from the tables,
    before the SLAs), or why there is none. opts: ran (bool), reason, model, provider."""
    o = opts or {}
    if not o.get("ran"):
        return [{"title": "For the WAR (written by ACT)", "status": "warning", "lines": ["ACT did not run: %s" % (o.get("reason") or "unknown")],
                 "text": "The version from the tables, above, is complete without it."}]
    pa = servicenow_war_act_parse(raw)
    if not pa["parsed"] and pa.get("form") == "action":
        a = pa.get("action") or {}
        return [{"title": "For the WAR (written by ACT)", "status": "warning",
                 "text": "ACT did not write the WAR: instead of answering, the model asked to run a command - evidence mode runs none, and the "
                         "job tells it so. Run the job again; if it repeats, keep these two lines. The version from the tables, above, is "
                         "complete without it.",
                 "lines": ["It wanted to run: %s" % a.get("command"), "Its reason: %s" % (a.get("thought") or "(none given)")]}]
    if not pa["parsed"]:
        said = [_act_clean(x) for x in str(raw or "").splitlines() if _act_clean(x)]
        return [{"title": "For the WAR (written by ACT)", "status": "warning",
                 "text": "%s - its answer as it came (the first 40 lines); the version from the tables, above, is complete without it." % pa["error"],
                 "lines": [x[:400] for x in said[:40]] or ["(empty)"]}]
    tag = " (%s%s)" % (o.get("provider") or "ACT", (" " + str(o["model"])) if o.get("model") else "")
    form = {"prose": " ACT gave no JSON block this time: its words were sorted into these sections by their headings.",
            "json-cut": " ACT's answer was cut short at the model's output limit and its last bullet is lost: site_act_env: {ACT_MAX_TOKENS: \"8192\"} "
                        "in all.yml gives it room."}.get(pa.get("form") or "", "")
    out = [{"title": "For the WAR (written by ACT): summary",
            "text": "The other version of the WAR: written by GenAI%s from the tables above - use it or the one-line-per-task version "
                    "above it, and read it before you paste it: it can only be as right as those tables.%s" % (tag, form),
            "lines": [pa["overall"] or "(ACT gave no summary)"]},
           {"title": "For the WAR (written by ACT): accomplishments", "lines": pa["accomplishments"] or ["(ACT named no accomplishment)"]}]
    if pa["in_progress"]:
        out.append({"title": "For the WAR (written by ACT): in progress", "lines": pa["in_progress"]})
    if pa["issues"]:
        out.append({"title": "For the WAR (written by ACT): what deserves attention", "status": "warning", "lines": pa["issues"]})
    return out


class FilterModule(object):
    def filters(self):
        return {"servicenow_war_report": servicenow_war_report, "servicenow_war_name_list": servicenow_war_name_list,
                "servicenow_war_add_act": servicenow_war_add_act,
                "servicenow_war_web_folder": servicenow_war_web_folder, "servicenow_war_xlsx": servicenow_war_xlsx,
                "servicenow_war_lines": servicenow_war_lines, "servicenow_war_names": servicenow_war_names,
                "servicenow_war_act_evidence": servicenow_war_act_evidence, "servicenow_war_act_task": servicenow_war_act_task,
                "servicenow_war_act_parse": servicenow_war_act_parse, "servicenow_war_act_section": servicenow_war_act_section}
