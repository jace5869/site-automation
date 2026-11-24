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
U = datetime.timezone.utc
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
    now_utc = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(U)
    now = now_utc.astimezone(tz)
    out["now"] = _show(now)
    lead = datetime.timedelta(minutes=max(0, int(min_lead or 0)))

    def candidates(day):
        """The moments (UTC) the time stands for that day: one; two when the clocks go back (the first, then the second);
        none when the clocks skip it - with the first's UTC moment as if it existed (to tell 'passed' from 'ahead')."""
        wall = datetime.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
        u0 = wall.astimezone(U)
        if u0.astimezone(tz).replace(tzinfo=None) != wall.replace(tzinfo=None):
            return [], u0, wall
        out_ = [(u0, wall)]
        w1 = wall.replace(fold=1)
        if w1.utcoffset() != wall.utcoffset():
            out_.append((w1.astimezone(U), w1))
        return out_, u0, wall

    pick, which = None, ""
    for n, day in enumerate((now.date(), now.date() + datetime.timedelta(days=1))):
        cands, u0, wall = candidates(day)
        if not cands:                                  # the clocks skip it that night
            if n == 0 and now.replace(tzinfo=None) >= wall.replace(tzinfo=None):   # the wall clock is past it (no fold that day)
                continue                               # the clocks have jumped past it today: tomorrow
            out["day"] = "today" if n == 0 else "tomorrow"
            out["error"] = ("%s does not exist on %s in %s (the clocks go forward that night): pick another time"
                            % (out["time"], wall.strftime("%Y-%m-%d"), out["zone"]))
            return out
        ahead = [(u, wl, i) for i, (u, wl) in enumerate(cands) if u > now_utc]
        if not ahead:
            continue
        pick = ahead[0]
        out["day"] = "today" if n == 0 else "tomorrow"
        if n == 1:
            out["note"] = "%s today has passed (it is %s now): tomorrow." % (out["time"], now.strftime("%H:%M %Z"))
        if len(cands) > 1:
            which = "the first one" if pick[2] == 0 else "the second one (the first has passed)"
            out["note"] = (out["note"] + " " if out["note"] else "") + (
                "%s happens twice on %s (the clocks go back that night): %s, %s." % (out["time"], pick[1].strftime("%Y-%m-%d"), which,
                                                                                    pick[1].strftime("%Z")))
        break
    if pick is None:                                   # cannot happen (tomorrow is always ahead); said, never guessed
        out["error"] = "%s could not be placed in %s" % (out["time"], out["zone"])
        return out
    if pick[0] - now_utc < lead:                       # in real time (UTC), not on the wall clock
        out["error"] = ("%s is less than %d minutes away (it is %s now): pick a later time - this job never restarts "
                        "right away (win_restart_min_lead_minutes)" % (out["time"], lead.seconds // 60, now.strftime("%H:%M %Z")))
        return out
    out["utc"] = pick[0].strftime("%Y-%m-%dT%H:%M:%SZ")
    out["site"] = _show(pick[1])
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
               "removed": _list(r.get("removed")), "error": "", "swis": h.get("swis") or {}}
        if h.get("unreached"):
            row.update(status="not reached", level="critical", error=str(h["unreached"])[:300])
            rows.append(row)
            continue
        if not r or r.get("error"):
            err = str(r.get("error") or "no result")[:400]
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
    sw_warn = any(x["swis"].get("level") == "warning" for x in rows)
    status = "critical" if bad else ("warning" if sw_warn else ("ok" if sched or not check else "info"))
    summary = [{"label": "Servers", "value": n, "status": "info", "note": "the Limit"},
               {"label": "Would restart" if check else "Scheduled", "value": sched, "status": "warning" if check and sched else ("ok" if sched else "info"),
                "note": at}]
    if skipped:
        summary.append({"label": "Skipped", "value": skipped, "status": "info", "note": "no reboot pending"})
    summary.append({"label": "Reboot pending", "value": pending, "status": "warning" if pending else "ok",
                    "note": "%d of %d read%s" % (pending, read, "" if pending else " - none pending")})
    if bad:
        summary.append({"label": "Failed or not reached", "value": bad, "status": "critical", "note": "see the table"})
    if any(x["swis"].get("text") for x in rows):
        un = len([x for x in rows if x["swis"].get("level") == "ok"])
        warn = len([x for x in rows if x["swis"].get("level") == "warning"])
        summary.append({"label": "SolarWinds - would unmanage" if check else "SolarWinds unmanaged", "value": un,
                        "status": "warning" if warn else "info", "note": ("%d not: see the table" % warn) if warn else (o.get("swis_window") or "")})
    res = []
    for x in rows:
        r = x["status"]
        if x["status"] in ("scheduled", "would restart"):
            r = "Scheduled" if x["status"] == "scheduled" else "Would restart"          # the time: the next column, the title
        elif x["status"] == "skipped":
            r = "Skipped: no reboot pending"
        elif x["error"]:
            r = "%s: %s" % (x["status"].capitalize(), x["error"])
        if x["status"] == "failed":
            pass
        elif x["replaced"] or (check and x["ours"] and x["status"] == "would restart"):
            r += " - replaces the restart at %s" % "; ".join(t.get("next_run") or t.get("start") or "?" for t in (x["replaced"] or x["ours"]))
        elif x["status"] == "skipped" and x["removed"]:
            r += "; the earlier restart at %s was removed" % "; ".join(t.get("next_run") or t.get("start") or "?" for t in x["removed"])
        elif x["status"] == "skipped" and x["ours"] and check:
            r += "; the earlier restart at %s would be removed" % "; ".join(t.get("next_run") or t.get("start") or "?" for t in x["ours"])
        res.append(r)
    sw_on = any(x["swis"].get("text") for x in rows)
    table = {"title": "Servers", "columns": ["Server", "Reboot pending", "Why", "Up since", "Restart at (server time)", "Result"],
             "kinds": ["nw", "", "wrap", "", "wrap", "wrap"], "hidden_columns": ["Up since"],
             "rows": [[_short(x["host"]), x["pending"] or "-", x["why"] or "-", x["up"] or "-",
                       (re.sub(r" \(.*\)$", "", x["at_local"]) if x["status"] in ("scheduled", "would restart") else "-") or "-", res[i]]
                      for i, x in enumerate(rows)],
             "row_status": [x["level"] if x["level"] in ("critical",) else "" for x in rows],
             "cell_status": [["", "warning" if x["pending"] == "yes" else "", "", "", "", _cell(x["status"])] for x in rows],
             "column_titles": ["Server", "A reboot is pending now", "Why it is pending", "Last boot (server time) and uptime",
                               "The restart in the server's own time zone", "What the job did"]}
    if sw_on:                                   # the SolarWinds node: one more column (unmanaged for the window, or why not)
        table["columns"].append("SolarWinds")
        table["kinds"].append("wrap")
        table["column_titles"].append("The server's SolarWinds node during the restart")
        table["hidden_columns"].append("Restart at (server time)")      # under Columns: the server names stay on one line at 1280 px
        for i, x in enumerate(rows):
            txt = re.sub(r"^SolarWinds: ", "", x["swis"].get("text") or "-")
            # the node's caption only when it is not the server's own name (the row says that already)
            txt = re.sub(r"^node (\S+) \((N:\d+)\)", lambda m_: m_.group(2) if m_.group(1).split(".")[0].lower() == _short(x["host"]).lower()
                         else "%s (%s)" % (m_.group(1), m_.group(2)), txt)
            table["rows"][i].append(txt)
            table["cell_status"][i].append({"warning": "warning", "ok": "ok"}.get(x["swis"].get("level"), ""))
    lines = ["Restart time: %s in %s = %s (%s)." % (when.get("time"), when.get("zone"), at, when.get("day"))]
    if when.get("note"):
        lines.append(when["note"])
    lines.append("Only if a reboot is pending: %s%s." % ("yes" if o.get("only_if_pending") else "no",
                                                       " - checked now and again at the restart time" if o.get("only_if_pending") else ""))
    lines.append("Reason: %s" % (o.get("comment") or "-"))
    if o.get("swis_window"):
        lines.append("SolarWinds: each server's node %s %s (%s); SolarWinds manages it again by itself at the end."
                     % ("would be unmanaged" if check else "unmanaged", o["swis_window"], o.get("swis_span") or ""))
        if o.get("only_if_pending"):
            lines.append("With only if pending, the node is unmanaged for the window even when the server finds nothing pending "
                         "at the restart time and does not restart.")
    elif o.get("swis_note"):
        lines.append(o["swis_note"])
    for t in o.get("swis_times") or []:
        lines.append("SolarWinds returns times as %s (the window read back after the unmanage)." % ("UTC" if t == "utc" else "local time"))
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
    rows_by = dict((_short(x["host"]), x) for x in rows)
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
    sw = [x for x in rows if (x["swis"] or {}).get("text")]
    if sw:
        trs = [r + [re.sub(r"^SolarWinds: ", "", (rows_by[r[0]]["swis"] or {}).get("text") or "-")] for r in trs]
        cs = [c + [{"warning": "warning", "ok": "ok"}.get((rows_by[trs[i][0]]["swis"] or {}).get("level"), "")] for i, c in enumerate(cs)]
    sections = [{"title": "Servers", "columns": ["Server", "Task", "Was set for (server time)", "Reason", "Result"] + (["SolarWinds"] if sw else []),
                 "kinds": ["nw", "wrap", "", "wrap", "wrap"] + (["wrap"] if sw else []), "rows": trs, "cell_status": cs}]
    if check and done:
        sections.append({"title": "Run it for real", "lines": [
            "This was a Check run: nothing was changed. Launch it again without Check to remove the tasks."]})
    sw_lines = ([o["swis_note"]] if o.get("swis_note") else []) + [
        "SolarWinds returns times as %s (the window read back after the unmanage)." % ("UTC" if t == "utc" else "local time")
        for t in o.get("swis_times") or []]
    if sw_lines:
        sections.append({"title": "SolarWinds", "lines": sw_lines})
    h_counts = [[done, "would be cancelled" if check else "cancelled", "ok"]]
    h_counts += [[c, w_, st] for c, w_, st in ((_n(model, "nothing to cancel"), "had none", "info"), (bad, "failed or not reached", "critical")) if c]
    sw_warn = any((x["swis"] or {}).get("level") == "warning" for x in rows)
    return {"title": title, "status": "critical" if bad else ("warning" if sw_warn else "ok"), "summary": summary, "sections": sections,
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
        if x["swis"].get("text"):
            bits.append(x["swis"]["text"])
        for t in x["removed"]:
            bits.append("removed " + _task_line(t))
        if x["status"] == "would remove":
            bits.append("; ".join(_task_line(t) for t in x["ours"]))
        out.append("%s: %s" % (_short(x["host"]), " - ".join(bits)))
    o = model.get("opts") or {}
    if o.get("swis_note"):
        out.append(o["swis_note"])
    elif o.get("swis_window"):
        out.append("SolarWinds window: %s (%s)." % (o["swis_window"], o.get("swis_span") or ""))
    for t in o.get("swis_times") or []:
        out.append("SolarWinds returns times as %s." % ("UTC" if t == "utc" else "local time"))
    return out or ["No server in this run."]


def _terms(text):
    """A host pattern -> its picking terms: split at , and : (not inside a [range]), without the exclusions (!x) and the
    narrowing ones (&x)."""
    out = []
    for t in re.split(r"[,:](?![^\[]*\])", str(text or "")):
        t = t.strip()
        if t and not t.startswith(("!", "&")):
            out.append(t)
    return out


def win_restart_selector(limit="", target=""):
    """The servers the person picked: the Limit's terms, else target's; [] when nothing real was picked (empty, all, *, or
    only exclusions) - the job then refuses."""
    for text in (limit, target):
        terms = [t for t in _terms(text) if t.lower() not in ("all", "*")]
        if terms:
            return terms
    return []


def _match(host, term):
    """A host against an Ansible host pattern term: * and ?, and a range web[01:03] (web01, web02, web03)."""
    out, i = "", 0
    for m in re.finditer(r"\[(\d+):(\d+)\]|\[([a-z]):([a-z])\]", term):
        out += _glob(term[i:m.start()])
        if m.group(1) is not None:
            a, b, w = int(m.group(1)), int(m.group(2)), len(m.group(1))
            out += "(?:%s)" % "|".join("%0*d" % (w, n) for n in range(a, b + 1))
        else:
            out += "[%s-%s]" % (m.group(3), m.group(4))
        i = m.end()
    out += _glob(term[i:])
    return re.fullmatch(out, host) is not None


def _glob(text):
    return "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in text)


def win_restart_unnamed(hosts, terms, groups=None):
    """The hosts of the run that no picked term names: a host name (any case; a short name too), a pattern of them (* ? [..]),
    or an inventory group the host is in. [] when every one was picked."""
    groups = groups or {}
    out = []
    for h in hosts or []:
        h_ = str(h).lower()
        ok = False
        for t in terms or []:
            tl = t.lower().lstrip("~")
            if t in groups and h in (groups.get(t) or []):
                ok = True
            elif tl in (h_, h_.split(".")[0]) or any(_match(x, tl) for x in (h_, h_.split(".")[0])):
                ok = True
            if ok:
                break
        if not ok:
            out.append(str(h))
    return out


def win_restart_hosts(target="", exclude_not=""):
    """The play's hosts: target's terms and the exclusion as separate comma-separated parts ('A,B,!site_excluded'), never
    'A,B:!site_excluded' (Ansible splits at the commas first: the exclusion would bind to B alone)."""
    parts = [t.strip() for t in str(target or "all").split(",") if t.strip()] or ["all"]
    ex = [t for t in re.split(r"[,:]", str(exclude_not or "")) if t.strip()]
    return ",".join(parts + ex)


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
    if any(t.startswith("@") for t in _terms(o.get("limit")) + _terms(o.get("target"))):
        bad.append("a Limit from a file (@file) is not taken here: pick the servers by name (or group) in the Limit")
    elif not win_restart_selector(o.get("limit"), o.get("target")):
        bad.append("no servers picked: set the Limit (or the Hosts box on the site portal) to the servers - an empty one, 'all' or "
                   "only exclusions ('!name') is refused; this job never acts on every server")
    elif o.get("resolved") is not None and not o["resolved"]:
        bad.append("the Limit matches no server")
    elif o.get("resolved") is not None and win_restart_unnamed(o["resolved"], win_restart_selector(o.get("limit"), o.get("target")),
                                                                o.get("groups") or {}):
        bad.append("in this run but not named in the Limit: %s - the job stops" % ", ".join(
            win_restart_unnamed(o["resolved"], win_restart_selector(o.get("limit"), o.get("target")), o.get("groups") or {})))
    elif o.get("excluded_in"):
        bad.append("left out by site_exclude_hosts but in this run: %s - the job stops" % ", ".join(o["excluded_in"]))
    elif int(o.get("hosts") or 0) > int(o.get("max_hosts") or 0):
        bad.append("%s servers in the Limit; at most %s in one run (win_restart_max_hosts)" % (o.get("hosts"), o.get("max_hosts")))
    return bad


_rs = None


def _swis():
    """plugins/module_utils/site_win_restart_swis.py (its window and time helpers), loaded by its path as the filters load
    each other."""
    global _rs
    if _rs is None:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("site_win_restart_swis_for_filters", os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_win_restart_swis.py"))
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils")
        import sys
        if here not in sys.path:
            sys.path.insert(0, here)                       # its own imports: site_swis, site_host_swis
        _rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_rs)
    return _rs


def _earlier(tasks, before=5, after=10):
    """The unmanage window an earlier restart of ours asked for ({from, until}, from its settings' restart moment), or {}."""
    for t in _list(tasks):
        at_ = (t or {}).get("at_utc") if isinstance(t, dict) else ""
        if at_:
            return _swis().window(at_, before, after)
    return {}


def win_restart_swis_items(hosts, found, window, mode="schedule", check=False, before=5, after=10):
    """hosts: [{host, result}] -> the Unmanage steps: one per server whose restart is scheduled (or, in Check, would be).
    Nothing else ever calls SolarWinds: no Remanage, not for a skip, a reschedule or a cancel."""
    out = []
    if mode != "schedule":
        return out
    for h in hosts or []:
        r, host = h.get("result") or {}, str(h.get("host"))
        if not r or r.get("error"):
            continue
        f = (found or {}).get(host) or {}
        if (check and r.get("restart")) or (not check and r.get("scheduled")):
            out.append({"host": host, "node_id": ((f.get("node") or {}).get("node_id")), "name": f.get("name", ""),
                        "why": f.get("why", ""), "window": window,
                        "earlier": _earlier(r.get("replaced") if not check else r.get("ours"), before, after)})
    return out


def win_restart_swis_map(hosts, result, state="on", why="", zone="", before=5, after=10):
    """The SolarWinds line of each server: {host: {text, level}}. state: on, off, nocred, error (why)."""
    out = {}
    if state != "on":
        return out
    tz = _swis_tz(zone)
    for h in hosts or []:
        r, host = h.get("result") or {}, str(h.get("host"))
        got = (result or {}).get(host)
        if got and got.get("text"):
            out[host] = {"text": got["text"], "level": got.get("level", "info")}
        elif r and not r.get("error") and r.get("restart") is False:
            text = "SolarWinds: not unmanaged (skipped: no reboot pending)"
            e = _earlier(r.get("removed") or r.get("ours"), before, after)
            if e:
                text += "; the earlier restart's unmanage window (%s), if it was set, ends by itself" % _swis().shown(e, tz)
            out[host] = {"text": text, "level": "info"}
    return out


def _swis_tz(zone):
    z = str(zone or "").strip()
    if not z or z.upper() in UTC_NAMES or zoneinfo is None:
        return None
    try:
        return zoneinfo.ZoneInfo(z)
    except Exception:  # noqa: BLE001 - shown in UTC then
        return None


def win_restart_swis_cancel(hosts, check=False, zone="", before=5, after=10, state="on"):
    """Cancel never calls SolarWinds: the line of each server whose restart of ours goes - its unmanage window (if it was
    set) stays and ends by itself."""
    out = {}
    if state == "off":
        return out
    tz = _swis_tz(zone)
    for h in hosts or []:
        r, host = h.get("result") or {}, str(h.get("host"))
        if not r or r.get("error"):
            continue
        e = _earlier(r.get("ours") if check else r.get("removed"), before, after)
        if e:
            out[host] = {"level": "info", "text": "SolarWinds: the unmanage window %s (if it was set) %s and ends by itself; re-manage the "
                                                  "node by hand in SolarWinds if needed" % (_swis().shown(e, tz), "would stay" if check else "stays")}
    return out


def win_restart_swis_note(state, why="", check=False):
    """The one line when SolarWinds is not used this run."""
    return {"off": "SolarWinds: off (win_restart_solarwinds: false) - nodes stay managed.",
            "nocred": "SolarWinds: no credential - node stays managed (attach a SolarWinds credential that may manage nodes: kind solarwinds_manage).",
            "error": "SolarWinds: %s - nodes stay managed." % (why or "could not be read")}.get(state, "")


class FilterModule(object):
    def filters(self):
        return {"win_restart_when": win_restart_when, "win_restart_comment": win_restart_comment, "win_restart_parse": win_restart_parse,
                "win_restart_model": win_restart_model, "win_restart_doc": win_restart_doc, "win_restart_cancel_doc": win_restart_cancel_doc,
                "win_restart_summary_items": win_restart_summary_items, "win_restart_log": win_restart_log, "win_restart_check": win_restart_check,
                "win_restart_selector": win_restart_selector, "win_restart_hosts": win_restart_hosts,
                "win_restart_unnamed": win_restart_unnamed,
                "win_restart_swis_items": win_restart_swis_items, "win_restart_swis_map": win_restart_swis_map,
                "win_restart_swis_cancel": win_restart_swis_cancel,
                "win_restart_swis_note": win_restart_swis_note}
