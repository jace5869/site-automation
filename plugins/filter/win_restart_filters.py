# -*- coding: utf-8 -*-
"""Windows - schedule a restart / Windows - cancel a scheduled restart (playbooks/win_restart_schedule.yml,
playbooks/win_restart_cancel.yml, roles/win_restart; docs/WINDOWS.md "Schedule a restart").
- win_restart_when: the survey's 24-hour time (HH:MM) in the site's time zone -> one moment (UTC) for every server. A time
  today that has passed means tomorrow; one closer than win_restart_min_lead_minutes is refused (never a restart right
  away); a time the clocks skip (summer time starts) is refused; one they pass twice (summer time ends) is the first.
- win_restart_parse: what roles/win_restart/files/win_restart.ps1 printed on a server.
- win_restart_model / win_restart_doc / win_restart_cancel_doc: the plan and the report (one row per server).
- win_restart_comment: the shutdown comment (System event 1074): one line, no double quote, at most 500 characters."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import re

try:
    import zoneinfo
except ImportError:                                  # Python before 3.9
    zoneinfo = None

MARK = "###SITE-JSON### "
UTC_NAMES = ("UTC", "Z", "ETC/UTC", "GMT")
TIME = re.compile(r"^([01]?\d|2[0-3]):?([0-5]\d)$")
CHANGE = re.compile(r"^[A-Za-z0-9_-]{0,40}$")
CODE = re.compile(r"^[pu]:\d{1,3}:\d{1,5}$")
COMMENT_MAX = 500                                     # shutdown /c takes 512 characters
NOTE = ("How it works: each server gets a one-time scheduled task (SYSTEM) for this moment in its own time zone. At that "
        "time it runs shutdown /r with the reason as comment (System event 1074). With \"only if pending\" it checks again "
        "first and does nothing when no reboot is pending. A server that is off at that time does not restart later. "
        "Cancel: the job Windows - cancel a scheduled restart.")


def _zone(name):
    """The time zone named -> (tzinfo, '') or (None, why)."""
    name = str(name or "").strip()
    if not name:
        return None, ("no time zone: set report_time_zone (e.g. America/New_York) or win_restart_time_zone in the template's "
                      "Variables (UTC is allowed) - a restart is never planned in a time zone the job guessed")
    if name.upper() in UTC_NAMES:
        return datetime.timezone.utc, ""
    if zoneinfo is None:
        return None, "time zone %s: this execution environment's Python has no zoneinfo (3.9 or later)" % name
    try:
        return zoneinfo.ZoneInfo(name), ""
    except Exception:                                # noqa: BLE001 - an unknown name, or no tzdata
        return None, ("time zone %s is not one this execution environment knows (a name as timedatectl list-timezones "
                      "prints it, e.g. America/New_York; the EE needs the tzdata package)" % name)


def _show(dt):
    return dt.strftime("%a %Y-%m-%d %H:%M %Z").strip()


def win_restart_when(text, zone="", min_lead=5, now=None):
    """The restart time typed (HH:MM, 24-hour; 2230 too) in the time zone zone -> {ok, error, time, zone, utc
    (YYYY-MM-DDTHH:MM:SSZ), site (Tue 2026-11-24 22:30 CET), day (today/tomorrow), note, now}. now: an aware datetime
    (tests); default: now."""
    out = {"ok": False, "error": "", "time": "", "zone": str(zone or "").strip(), "utc": "", "site": "", "day": "", "note": "", "now": ""}
    m = TIME.match(str(text or "").strip())
    if not m:
        out["error"] = ("the restart time %r is not a 24-hour time: type HH:MM, e.g. 22:30 (00:00 to 23:59)"
                        % str(text or "").strip()[:20])
        return out
    hh, mm = int(m.group(1)), int(m.group(2))
    out["time"] = "%02d:%02d" % (hh, mm)
    tz, why = _zone(zone)
    if tz is None:
        out["error"] = why
        return out
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)
    out["now"] = _show(now)
    lead = datetime.timedelta(minutes=max(0, int(min_lead or 0)))
    day = now.date()
    wall = datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
    if wall <= now:
        day = day + datetime.timedelta(days=1)
        wall = datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        out["day"] = "tomorrow"
        out["note"] = "%s today has passed (it is %s now): tomorrow." % (out["time"], now.strftime("%H:%M"))
    else:
        out["day"] = "today"
        if wall - now < lead:
            out["error"] = ("%s is less than %d minutes away (it is %s now): pick a later time - this job never restarts "
                            "right away (win_restart_min_lead_minutes)" % (out["time"], lead.seconds // 60, now.strftime("%H:%M")))
            return out
    back = wall.astimezone(datetime.timezone.utc).astimezone(tz)
    if back.replace(tzinfo=None) != wall.replace(tzinfo=None):
        out["error"] = ("%s does not exist on %s in %s (the clocks go forward that night): pick another time"
                        % (out["time"], wall.strftime("%Y-%m-%d"), out["zone"]))
        return out
    if wall.replace(fold=1).utcoffset() != wall.utcoffset():
        out["note"] = (out["note"] + " " if out["note"] else "") + (
            "%s happens twice on %s (the clocks go back that night): the first one, %s." % (out["time"], wall.strftime("%Y-%m-%d"), wall.strftime("%Z")))
    out["utc"] = wall.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out["site"] = _show(wall)
    out["ok"] = True
    return out


def win_restart_comment(reason, change="", job=""):
    """The shutdown comment and the task's description: '<change>: <reason> (site-automation, AAP job N)' - one line, no
    double quote (shutdown's /c), at most 500 characters."""
    def clean(s):
        return " ".join(str(s or "").replace('"', "'").split())
    tail = " (site-automation%s)" % ((", AAP job %s" % clean(job)) if clean(job) else "")
    head = ("%s: " % clean(change).upper()) if clean(change) else ""
    body = clean(reason)[:COMMENT_MAX - len(head) - len(tail)]
    return head + body + tail


def win_restart_parse(stdout_lines):
    """The script's output lines -> its result, or {"error": why}."""
    lines = [str(x) for x in (stdout_lines or []) if str(x).startswith(MARK)]
    if not lines:
        return {"error": "the script printed no result"}
    try:
        r = json.loads(lines[-1][len(MARK):])
    except ValueError as e:
        return {"error": "the script's result is not JSON: %s" % e}
    return r if isinstance(r, dict) else {"error": "the script's result is not an object"}


def _short(host):
    h = str(host or "")
    return h if re.match(r"^\d+\.\d+\.\d+\.\d+$", h) else h.split(".")[0]


def _list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _uptime(r):
    d = r.get("uptime_days")
    if d is None or r.get("boot", "") == "":
        return ""
    return "%s (%s d)" % (r.get("boot"), ("%.1f" % float(d)).rstrip("0").rstrip("."))


def win_restart_model(hosts, opts=None):
    """hosts: [{host, result (the script's), unreached}]; opts: {mode: schedule|cancel, check, only_if_pending,
    count_file_renames} -> {rows: [{host, status, level, pending, why, up, at_local, ours, replaced, removed, error}],
    counts, opts}. status: scheduled, would restart, skipped, removed, would remove, nothing to cancel, failed, not reached."""
    o = dict(opts or {})
    check, cancel = bool(o.get("check")), o.get("mode") == "cancel"
    rows = []
    for h in hosts or []:
        r = h.get("result") or {}
        row = {"host": str(h.get("host")), "status": "", "level": "info", "pending": "", "why": "", "up": _uptime(r),
               "at_local": r.get("at_local", ""), "ours": _list(r.get("ours")), "replaced": _list(r.get("replaced")),
               "removed": _list(r.get("removed")), "error": ""}
        if h.get("unreached"):
            row.update(status="not reached", level="critical", error=str(h["unreached"])[:300])
            rows.append(row)
            continue
        if not r or r.get("error"):
            err = str(r.get("error") or "no result")[:300]
            if row["replaced"]:
                err += " - the earlier restart of ours was removed: none is scheduled now"
            row.update(status="failed", level="critical", error=err)
            rows.append(row)
            continue
        if cancel:
            if check:
                row["status"] = "would remove" if row["ours"] else "nothing to cancel"
            else:
                row["status"] = "removed" if row["removed"] else "nothing to cancel"
            row["level"] = "ok" if row["status"] in ("removed", "would remove") else "info"
            rows.append(row)
            continue
        reasons = [str(x) for x in _list(r.get("reasons")) if x]
        probes = r.get("probes") or {}
        row["pending"] = "yes" if reasons else "no"
        why = ", ".join(reasons)
        if probes.get("file_renames") and "file rename operations" not in reasons:
            why = (why + "; " if why else "") + "file rename operations set (not counted: win_restart_count_file_renames)"
        if probes.get("rename"):
            why += " (%s -> %s)" % (probes.get("active_name", ""), probes.get("pending_name", ""))
        row["why"] = why
        if not r.get("restart"):
            row.update(status="skipped", level="info")
        elif check:
            row.update(status="would restart", level="warning")
        elif r.get("scheduled"):
            row.update(status="scheduled", level="ok")
        else:
            row.update(status="failed", level="critical", error="no task was registered")
        rows.append(row)
    order = {"not reached": 0, "failed": 1, "scheduled": 2, "would restart": 2, "removed": 2, "would remove": 2, "skipped": 3,
             "nothing to cancel": 3}
    rows.sort(key=lambda x: (order.get(x["status"], 9), x["host"].lower()))
    counts = {}
    for x in rows:
        counts[x["status"]] = counts.get(x["status"], 0) + 1
    return {"rows": rows, "counts": counts, "opts": o}


def _n(model, *keys):
    return sum(model["counts"].get(k, 0) for k in keys)


def _cell(status):
    return {"scheduled": "ok", "removed": "ok", "would restart": "warning", "would remove": "warning", "failed": "critical",
            "not reached": "critical"}.get(status, "")


def _task_line(t):
    return "%s%s at %s" % (t.get("path", ""), t.get("name", ""), t.get("next_run") or t.get("start") or "?")


def win_restart_doc(model, when):
    """The schedule job's report: At a glance tiles, one row per server, the time and the reason."""
    o, rows = model["opts"], model["rows"]
    check = bool(o.get("check"))
    n = len(rows)
    sched = _n(model, "scheduled", "would restart")
    skipped = _n(model, "skipped")
    bad = _n(model, "failed", "not reached")
    pending = len([x for x in rows if x["pending"] == "yes"])
    read = len([x for x in rows if x["pending"]])
    at = when.get("site", "")
    if check:
        title = "Windows restart plan: %d would restart at %s" % (sched, at)
    else:
        title = "Windows restart: %d scheduled for %s" % (sched, at)
    title += "".join(", %d %s" % (c, w) for c, w in ((skipped, "skipped"), (bad, "failed or not reached")) if c)
    if check:
        title += " (nothing changed)"
    status = "critical" if bad else ("ok" if sched or not check else "info")
    summary = [{"label": "Servers", "value": n, "status": "info", "note": "the Limit"},
               {"label": "Would restart" if check else "Scheduled", "value": sched, "status": "warning" if check and sched else ("ok" if sched else "info"),
                "note": at}]
    if skipped:
        summary.append({"label": "Skipped", "value": skipped, "status": "info", "note": "no reboot pending"})
    summary.append({"label": "Reboot pending", "value": pending, "status": "warning" if pending else "ok",
                    "note": "%d of %d read%s" % (pending, read, "" if pending else " - none pending")})
    if bad:
        summary.append({"label": "Failed or not reached", "value": bad, "status": "critical", "note": "see the table"})
    res = []
    for x in rows:
        r = x["status"]
        if x["status"] in ("scheduled", "would restart"):
            r = "%s at %s" % ("Scheduled" if x["status"] == "scheduled" else "Would restart", at)
        elif x["status"] == "skipped":
            r = "Skipped: no reboot pending"
        elif x["error"]:
            r = "%s: %s" % (x["status"].capitalize(), x["error"])
        if x["status"] == "failed":
            pass
        elif x["replaced"] or (check and x["ours"] and x["status"] == "would restart"):
            r += " - replaces %s" % "; ".join(_task_line(t) for t in (x["replaced"] or x["ours"]))
        elif x["ours"] and x["status"] == "skipped":
            r += " - an earlier one of ours stays: %s" % "; ".join(_task_line(t) for t in x["ours"])
        res.append(r)
    table = {"title": "Servers", "columns": ["Server", "Reboot pending", "Why", "Up since", "Restart at (server time)", "Result"],
             "kinds": ["", "", "wrap", "", "wrap", "wrap"],
             "rows": [[_short(x["host"]), x["pending"] or "-", x["why"] or "-", x["up"] or "-",
                       (re.sub(r" \(.*\)$", "", x["at_local"]) if x["status"] in ("scheduled", "would restart") else "-") or "-", res[i]]
                      for i, x in enumerate(rows)],
             "row_status": [x["level"] if x["level"] in ("critical",) else "" for x in rows],
             "cell_status": [["", "warning" if x["pending"] == "yes" else "", "", "", "", _cell(x["status"])] for x in rows],
             "column_titles": ["Server", "A reboot is pending now", "Why it is pending", "Last boot (server time) and uptime",
                               "The restart in the server's own time zone", "What the job did"]}
    lines = ["Restart time: %s in %s = %s (%s)." % (when.get("time"), when.get("zone"), at, when.get("day"))]
    if when.get("note"):
        lines.append(when["note"])
    lines.append("Only if a reboot is pending: %s%s." % ("yes" if o.get("only_if_pending") else "no",
                                                       " - checked now and again at the restart time" if o.get("only_if_pending") else ""))
    lines.append("Reason: %s" % (o.get("comment") or "-"))
    sections = [table, {"title": "When and why", "lines": lines}]
    if check and sched:
        sections.append({"title": "Run it for real", "lines": [
            "This was a Check run: nothing was changed. Launch it again without Check to schedule the restart."]})
    if bad:
        sections.append({"title": "What failed", "status": "critical",
                         "lines": ["%s: %s" % (_short(x["host"]), x["error"]) for x in rows if x["error"]]})
    h_counts = [[sched, "would restart" if check else "scheduled", "warning" if check else "ok"]]
    h_counts += [[c, w_, st] for c, w_, st in ((skipped, "skipped", "info"), (bad, "failed or not reached", "critical")) if c]
    return {"title": title, "status": status, "subtitle": "%s (%s)" % (at, when.get("zone")), "summary": summary,
            "head_name": "Windows restart plan" if check else "Windows restart", "head_counts": h_counts,
            "head_scope": ["%s (%s)" % (at, when.get("zone"))] + (["nothing changed"] if check else []),
            "sections": sections, "footer": NOTE + (" DRY RUN (Check): nothing was changed." if check else "")}


def win_restart_cancel_doc(model):
    """The cancel job's report: one row per server, each task of ours it found (and removed)."""
    o, rows = model["opts"], model["rows"]
    check = bool(o.get("check"))
    done = _n(model, "removed", "would remove")
    bad = _n(model, "failed", "not reached")
    title = "Windows restart cancel%s: %d %s" % (" plan" if check else "", done, "would be cancelled" if check else "cancelled")
    title += "".join(", %d %s" % (c, w) for c, w in ((_n(model, "nothing to cancel"), "had none"), (bad, "failed or not reached")) if c)
    if check:
        title += " (nothing changed)"
    trs, cs = [], []
    for x in rows:
        tasks = (x["removed"] if x["status"] == "removed" else x["ours"]) or [{}]
        for t in tasks:
            trs.append([_short(x["host"]), ("%s%s" % (t.get("path", ""), t.get("name", ""))) if t else "-",
                        (t.get("next_run") or t.get("start") or "-") if t else "-", t.get("description", "-") if t else "-",
                        x["status"] + ((": " + x["error"]) if x["error"] else "")])
            cs.append(["", "", "", "", _cell(x["status"])])
    summary = [{"label": "Servers", "value": len(rows), "status": "info", "note": "the Limit"},
               {"label": "Would be cancelled" if check else "Cancelled", "value": done, "status": "ok" if done else "info",
                "note": "our restart tasks"}]
    if bad:
        summary.append({"label": "Failed or not reached", "value": bad, "status": "critical", "note": "see the table"})
    sections = [{"title": "Servers", "columns": ["Server", "Task", "Was set for (server time)", "Reason", "Result"],
                 "kinds": ["", "wrap", "", "wrap", "wrap"], "rows": trs, "cell_status": cs}]
    if check and done:
        sections.append({"title": "Run it for real", "lines": [
            "This was a Check run: nothing was changed. Launch it again without Check to remove the tasks."]})
    h_counts = [[done, "would be cancelled" if check else "cancelled", "ok"]]
    h_counts += [[c, w_, st] for c, w_, st in ((_n(model, "nothing to cancel"), "had none", "info"), (bad, "failed or not reached", "critical")) if c]
    return {"title": title, "status": "critical" if bad else "ok", "summary": summary, "sections": sections,
            "head_name": "Windows restart cancel", "head_counts": h_counts, "head_scope": ["nothing changed"] if check else None,
            "footer": "Only our tasks (name starts with %s) are removed; a restart already counting down is not stopped (shutdown /a)."
                      % (o.get("task_name") or "site-automation restart") + (" DRY RUN (Check): nothing was changed." if check else "")}


def win_restart_summary_items(model, when=None):
    """The plan, for the portal's job page (site_approval_summary): one item per server."""
    out = []
    for x in model["rows"]:
        lv = "critical" if x["level"] == "critical" else ("warning" if x["status"] in ("would restart", "scheduled", "would remove", "removed") else "info")
        what = {"would restart": "restart at %s" % (when or {}).get("site", ""), "scheduled": "restart at %s" % (when or {}).get("site", ""),
                "skipped": "skipped: no reboot pending"}.get(x["status"], x["status"])
        out.append({"host": _short(x["host"]), "what": what + ((": " + x["error"]) if x["error"] else ""), "level": lv})
    return out


def win_restart_log(model, when=None):
    """The job output's lines: one per server."""
    at = (when or {}).get("site", "")
    out = []
    for x in model["rows"]:
        bits = [x["status"].upper()]
        if x["status"] in ("scheduled", "would restart"):
            bits.append("at %s (server: %s)" % (at, x["at_local"]))
        if x["pending"]:
            bits.append("pending: %s" % (x["why"] or "no"))
        if x["error"]:
            bits.append(x["error"])
        for t in x["replaced"]:
            bits.append("replaced " + _task_line(t))
        for t in x["removed"]:
            bits.append("removed " + _task_line(t))
        if x["status"] == "would remove":
            bits.append("; ".join(_task_line(t) for t in x["ours"]))
        out.append("%s: %s" % (_short(x["host"]), " - ".join(bits)))
    return out or ["No server in this run."]


def win_restart_check(o):
    """The launch's answers -> [] or the reasons the job refuses (nothing is read or changed then)."""
    o = dict(o or {})
    bad = []
    if not str(o.get("reason") or "").strip():
        bad.append("Reason is empty: say why the servers restart (it goes into the shutdown comment and the report)")
    if not CHANGE.match(str(o.get("change") or "").strip()):
        bad.append("the change number is letters, digits, - and _ only (e.g. CHG0012345)")
    if not CODE.match(str(o.get("code") or "").strip()):
        bad.append("win_restart_reason_code is p:<major>:<minor> (e.g. p:0:0)")
    try:
        if not 0 <= int(o.get("delay")) <= 600:
            raise ValueError
    except (TypeError, ValueError):
        bad.append("win_restart_delay_seconds is 0 to 600")
    if not o.get("limited"):
        bad.append("no Limit: pick the servers (the Limit, or the Hosts box on the site portal) - this job never restarts every server")
    elif int(o.get("hosts") or 0) > int(o.get("max_hosts") or 0):
        bad.append("%s servers in the Limit; at most %s in one run (win_restart_max_hosts)" % (o.get("hosts"), o.get("max_hosts")))
    return bad


class FilterModule(object):
    def filters(self):
        return {"win_restart_when": win_restart_when, "win_restart_comment": win_restart_comment, "win_restart_parse": win_restart_parse,
                "win_restart_model": win_restart_model, "win_restart_doc": win_restart_doc, "win_restart_cancel_doc": win_restart_cancel_doc,
                "win_restart_summary_items": win_restart_summary_items, "win_restart_log": win_restart_log, "win_restart_check": win_restart_check}
