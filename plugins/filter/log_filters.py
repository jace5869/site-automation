# -*- coding: utf-8 -*-
"""Filters for the log analysis (roles/log_analysis: playbooks/log_analysis.yml for a group of servers,
playbooks/vm_log_analysis.yml for vCenter and its ESXi hosts through vCenter's API).

What to read comes from the settings and the profiles (log_settings) and the window (log_window). Every
source ends up as the same entries - {host, source, time, level, app, message} - and the AAP side
condenses them: each line becomes a signature (its words with the timestamps, numbers, hex, UUIDs,
addresses and quoted values taken out), the same signature on several hosts is one pattern, and the
patterns are ranked (most severe, then most hosts, then most lines). The top ones are the problems ACT
analyzes, in the same form as the VMware alarms analysis (P1, P2 ...; the BEGIN_ACT_ANALYSIS JSON read
back by act_analysis_parse). The report is in the layout roles/site_email renders."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import fnmatch
import json
import math
import re
import time

LEVELS = ("critical", "error", "warning")
LEVEL_RANK = {"critical": 0, "error": 1, "warning": 2, "": 3}
LEVEL_STATUS = {"critical": "critical", "error": "critical", "warning": "warning"}
MARK_BEGIN, MARK_END = "BEGIN_ACT_ANALYSIS", "END_ACT_ANALYSIS"
SOURCE_LABEL = {"linux": "Linux", "windows": "Windows", "vcsa": "vCenter appliance", "vcenter": "vCenter (API)",
                "esxi": "ESXi (through vCenter)"}
PROFILE_KEYS = ("files", "units", "identifiers", "containers", "win_logs", "win_providers", "win_event_ids", "win_files",
                "match", "ignore")

# words that say how bad a line is, when its source gives no level (a text file)
_CRIT_WORDS = re.compile(r"\b(fatal|panic|emerg(ency)?|alert|crit(ical)?|segfault|out of memory|oom[- ]?kill(er|ed)?|"
                         r"kernel bug|corrupt(ed|ion)?|psod|purple screen|all paths down|apd|permanent device loss|pdl)\b", re.I)
_ERR_WORDS = re.compile(r"\b(err(or|ors)?|fail(ed|ure|s|ing)?|exception|denied|refused|cannot|can't|unable|timed? ?out|"
                        r"unreachable|not responding|lost|abort(ed)?|severe)\b", re.I)

# the parts of a line that change from one occurrence to the next
_NORMALIZE = [
    (re.compile(r"^\s*\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\]?\s*"), ""),     # a leading ISO time
    (re.compile(r"^\s*[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+"), ""),                                  # a leading syslog time
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\b"), "<time>"),
    (re.compile(r"\b\d\d:\d\d:\d\d(\.\d+)?\b"), "<time>"),
    (re.compile(r"\b(\d{1,3}\.){3}\d{1,3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"\b([0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I), "<ip>"),
    (re.compile(r"\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\"[^\"]{0,200}\"|'[^']{0,200}'"), "<q>"),
    (re.compile(r"\[\d+\]"), "[<n>]"),
    (re.compile(r"\b\d+(\.\d+)*\b"), "<n>"),
    (re.compile(r"(<n>[\s,]*){2,}"), "<n> "),
    (re.compile(r"\s+"), " "),
]


def _t(iso):
    """'2026-10-05T03:52:47Z' -> '2026-10-05 03:52:47'."""
    return str(iso or "").replace("T", " ").replace("Z", "")[:19]


def _hours(h):
    try:
        h = float(h)
    except (TypeError, ValueError):
        return str(h)
    return str(int(h)) if h == int(h) else str(round(h, 1))


def _short(name):
    """'web01.example.mil' -> 'web01'; an IP address as it is."""
    n = str(name or "")
    return n if n.replace(".", "").isdigit() else n.split(".")[0]


def _list(v, lines_only=False):
    """A setting that is a list, or text from a survey: 'a, b' or one per line (lines_only: one per line -
    a regular expression may hold a comma)."""
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        items = list(v)
    else:
        items = str(v).splitlines() if lines_only else re.split(r"[\n,]", str(v))
    return [str(x).strip() for x in items if str(x).strip()]


def _add(out, items):
    for x in items:
        if x not in out:
            out.append(x)
    return out


# ---- what to read --------------------------------------------------------------------------------
def log_settings(cfg, shipped=None, kind=""):
    """What one host reads: the base settings, the profiles switched on (the shipped ones, replaced or
    added to by profiles_extra) and the *_extra lists - each list once, in that order.

    cfg: the log_analysis_* settings without the prefix (profiles, profiles_extra, files, files_extra, units,
    identifiers, containers, win_logs, win_logs_extra, win_providers, win_event_ids, win_files, win_files_extra,
    match, match_extra, ignore, ignore_extra). kind: linux / windows / vcsa (vcsa switches its profile on).
    -> {profiles, unknown_profiles, files, units, identifiers, containers, win_logs, win_providers,
        win_event_ids: [{log, id}], win_files, match (one regular expression), ignore}"""
    c = cfg or {}
    table = dict(shipped or {})
    extra = c.get("profiles_extra") or {}
    if isinstance(extra, dict):
        table.update(extra)
    names = _list(c.get("profiles"))
    if kind == "vcsa" and "vcsa" not in names:
        names = ["vcsa"] + names
    used, unknown = [], []
    for n in names:
        (used if n in table else unknown).append(n)
    out = {"profiles": used, "unknown_profiles": unknown}
    for key in PROFILE_KEYS:
        regex = key in ("match", "ignore")
        vals = []
        if key not in ("match",):
            _add(vals, _list(c.get(key), regex))
        for n in used:
            _add(vals, _list((table.get(n) or {}).get(key), regex))
        if key in ("files", "win_logs", "win_files", "match", "ignore"):
            _add(vals, _list(c.get(key + "_extra"), regex))
        out[key] = vals
    words = [str(c.get("match") or "").strip()] + out.pop("match")
    out["match"] = "|".join(w for w in words if w)
    ids = []
    for x in out["win_event_ids"]:
        log, _, num = x.rpartition(":")
        if num.strip().isdigit():
            item = {"log": log.strip(), "id": int(num)}
            if item not in ids:
                ids.append(item)
    out["win_event_ids"] = ids
    return out


def _parse_time(s):
    s = str(s or "").strip()
    if not s:
        return None
    s = s.replace("T", " ").rstrip("Z").replace(" UTC", "").strip()
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, f).replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            pass
    return False


def log_window(hours, since="", until="", now=None):
    """The window to read: the last `hours` hours, or since .. until (UTC text such as '2026-10-23 08:00';
    until empty = now). -> {since_min, until_min (minutes before now; 0 = now), begin, end (ISO, end '' = now),
    hours, label, error}."""
    n = datetime.datetime.fromtimestamp(float(now) if now is not None else time.time(), tz=datetime.timezone.utc)
    iso = "%Y-%m-%dT%H:%M:%SZ"
    out = {"since_min": 0, "until_min": 0, "begin": "", "end": "", "hours": 0.0, "label": "", "error": ""}
    b, e = _parse_time(since), _parse_time(until)
    if b is False or e is False:
        out["error"] = "log_analysis_since / log_analysis_until must look like 2026-10-23 08:00 (UTC), not %s" % (
            since if b is False else until)
        return out
    if e is not None and e > n:
        e = None
    if b is None:
        try:
            h = float(hours)
        except (TypeError, ValueError):
            out["error"] = "log_analysis_hours must be a number, not %s" % hours
            return out
        if h <= 0:
            out["error"] = "log_analysis_hours must be more than 0"
            return out
        b = (e or n) - datetime.timedelta(hours=h)
    if e is not None and e <= b:
        out["error"] = "log_analysis_until (%s) is not after log_analysis_since (%s)" % (until, since)
        return out
    out["since_min"] = int(math.ceil((n - b).total_seconds() / 60.0))
    out["until_min"] = int((n - e).total_seconds() // 60) if e is not None else 0
    out["begin"] = b.strftime(iso)
    out["end"] = e.strftime(iso) if e is not None else ""
    out["hours"] = round(((e or n) - b).total_seconds() / 3600.0, 2)
    if str(since or "").strip():
        out["label"] = "%s to %s UTC" % (b.strftime("%Y-%m-%d %H:%M"), e.strftime("%Y-%m-%d %H:%M") if e else "now")
    else:
        out["label"] = "the last %s h%s" % (_hours(hours), " before %s UTC" % e.strftime("%Y-%m-%d %H:%M") if e else "")
    return out


def log_signature(message):
    """A line's signature: the words that stay the same from one occurrence to the next (lower case,
    at most 200 characters)."""
    s = str(message or "")
    for rx, rep in _NORMALIZE:
        s = rx.sub(rep, s)
    return s.strip().lower()[:200]


def log_level(level, message=""):
    """critical / error / warning from what the source says (a journal priority, an ESXi or vCenter level
    word), else from the line's words."""
    lv = str(level if level is not None else "").strip().lower()
    if lv in ("0", "1", "2", "emerg", "emergency", "alert", "crit", "critical", "cr", "fatal", "panic"):
        return "critical"
    if lv in ("3", "err", "error", "er", "errors"):
        return "error"
    if lv in ("4", "warn", "warning", "wa", "5", "6", "7", "notice", "info", "no", "in"):
        return "warning"
    m = str(message or "")
    if _CRIT_WORDS.search(m):
        return "critical"
    if _ERR_WORDS.search(m):
        return "error"
    return "warning"


def _source_key(source):
    """A file's name without its rotation (messages-20261020, vpxd-123.log, agent0.log.1 -> one source)."""
    s = str(source or "")
    s = re.sub(r"[-.]\d{8}(\.\w+)?$", "", s)
    s = re.sub(r"\.\d+$", "", s)
    s = re.sub(r"-\d+(\.log)$", r"\1", s)
    s = re.sub(r"(\D)\d+(\.log)$", r"\1\2", s)
    return s


# ---- reading what the collectors returned ---------------------------------------------------------
_SECTION = re.compile(r"^@@@ (\S+) ?(.*)$")
_TEXT_TS = re.compile(r"^\s*\[?(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(\.\d+)?(Z)?")


def _text_time(line):
    """A text log line's own time: ISO with Z when the line says UTC, else as written (no Z: local time)."""
    m = _TEXT_TS.match(line)
    if not m:
        return ""
    return "%sT%s%s" % (m.group(1), m.group(2), "Z" if m.group(4) else "")


def log_linux_entries(stdout, host="", opts=None):
    """What collect_linux.yml printed -> {'entries': [...], 'sources': [{name, lines, error}]}.
    Sections start with '@@@ journal <what>', '@@@ file <path>', '@@@ container <name>', '@@@ error <text>',
    '@@@ skipped <path> <why>'; the journal's lines are JSON (journalctl -o json) and are counted once when
    several journal reads (the general one, the units', the identifiers') return the same line."""
    o = opts or {}
    cap = int(o.get("max_line_chars") or 400)
    entries, sources, cur, seen = [], [], None, set()
    lines = stdout.splitlines() if isinstance(stdout, str) else list(stdout or [])
    for raw in lines:
        raw = str(raw)
        m = _SECTION.match(raw)
        if m:
            kind, arg = m.group(1), m.group(2).strip()
            if kind in ("journal", "file", "container"):
                name = {"journal": "journal" + (" (" + arg + ")" if arg else ""), "file": arg, "container": "container " + arg}[kind]
                cur = {"name": name, "lines": 0, "error": "", "kind": kind}
                sources.append(cur)
            elif kind == "error":
                if cur is not None:
                    cur["error"] = arg
                else:
                    sources.append({"name": "?", "lines": 0, "error": arg})
            elif kind == "skipped":
                path, _, why = arg.partition(" ")
                sources.append({"name": path, "lines": 0, "error": "", "skipped": why})
                cur = None
            continue
        if cur is None or not raw.strip():
            continue
        if cur["kind"] == "journal":
            try:
                j = json.loads(raw)
            except ValueError:
                continue
            msg = j.get("MESSAGE")
            if isinstance(msg, list):            # a message journald keeps as bytes
                try:
                    msg = bytes(msg).decode("utf-8", "replace")
                except (TypeError, ValueError):
                    msg = ""
            t = ""
            try:
                us = int(j.get("__REALTIME_TIMESTAMP") or 0)
                if us:
                    t = datetime.datetime.fromtimestamp(us / 1e6, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError, OverflowError, OSError):
                t = ""
            key = (j.get("__CURSOR") or "", j.get("__REALTIME_TIMESTAMP") or "", str(msg))
            if key in seen:
                continue
            seen.add(key)
            cur["lines"] += 1
            entries.append({"host": host, "source": "journal", "time": t, "level": log_level(j.get("PRIORITY"), msg),
                            "app": str(j.get("SYSLOG_IDENTIFIER") or j.get("_COMM") or ""),
                            "message": " ".join(str(msg or "").split())[:cap]})
        else:
            cur["lines"] += 1
            src = cur["name"]
            text = raw
            if cur["kind"] == "container":
                # podman logs --timestamps: "2026-10-23T08:00:01.123456789+00:00 the line"
                mt = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)\S*\s+(.*)$", raw)
                t = (mt.group(1) + "Z") if mt else ""
                text = mt.group(2) if mt else raw
            else:
                t = _text_time(raw)
            entries.append({"host": host, "source": src, "time": t, "level": log_level(None, text),
                            "app": src[len("container "):] if cur["kind"] == "container" else "",
                            "message": " ".join(text.split())[:cap]})
    for s in sources:
        s.pop("kind", None)
    return {"entries": entries, "sources": sources}


def log_win_entries(result, host="", opts=None):
    """What files/log_collect.ps1 printed (its ###SITE-JSON### object; the lines in facts) ->
    {'entries': [...], 'sources': [...]}."""
    o = opts or {}
    cap = int(o.get("max_line_chars") or 400)
    r = result or {}
    if isinstance(r.get("facts"), dict):          # the common Windows-check result: {findings, facts}
        r = r["facts"]
    out = []
    for e in r.get("entries") or []:
        msg = " ".join(str(e.get("message") or "").split())[:cap]
        lv = e.get("level")
        out.append({"host": host, "source": str(e.get("source") or ""), "time": str(e.get("time") or ""),
                    "level": lv if lv in LEVELS else log_level(None, msg), "app": str(e.get("app") or ""), "message": msg})
    return {"entries": out, "sources": list(r.get("sources") or [])}


# ---- condensing ------------------------------------------------------------------------------------
def _ignored(e, rxs):
    text = "%s %s" % (e.get("app") or "", e.get("message") or "")
    return any(rx.search(text) for rx in rxs)


def log_analysis(hosts, opts=None):
    """Every host's result -> the patterns, ranked, and the problems ACT analyzes.

    hosts: [{host, source (linux / windows / vcsa / vcenter / esxi), reachable, error, entries, sources}].
    opts: ignore (regexes: lines to leave out, besides each host's own 'ignore' list), begin / end (ISO, the window: a line whose own UTC time is
    outside is left out), min_count, levels (default all three), max_items (problems, default 30),
    examples (raw lines kept per pattern, default 3).
    -> {'hosts': [...without entries], 'patterns': n, 'all': [every pattern], 'problems': [the first
        max_items], 'not_analyzed': [the rest], 'lines', 'ignored', 'outside', 'critical', 'error', 'warning'}"""
    o = opts or {}
    cache = {}

    def compiled(items):
        key = tuple(items)
        if key not in cache:
            out = []
            for x in items:
                try:
                    out.append(re.compile(x, re.I))
                except re.error:
                    out.append(re.compile(re.escape(x), re.I))
            cache[key] = out
        return cache[key]
    common = _list(o.get("ignore"), True)
    levels = [str(x).lower() for x in (o.get("levels") or LEVELS)]
    nex = int(o.get("examples") or 3)
    min_count = int(o.get("min_count") or 1)
    begin, end = str(o.get("begin") or ""), str(o.get("end") or "")
    groups, order, lines, ignored, outside, host_rows = {}, [], 0, 0, 0, []
    for h in hosts or []:
        n_kept = 0
        rxs = compiled(_add(list(common), _list(h.get("ignore"), True)))
        for e in h.get("entries") or []:
            lines += 1
            t = e.get("time") or ""
            if t.endswith("Z") and ((begin and t < begin) or (end and t > end)):
                outside += 1
                continue
            if rxs and _ignored(e, rxs):
                ignored += 1
                continue
            lv = e.get("level") or log_level(None, e.get("message"))
            if lv not in levels:
                continue
            sig = log_signature(e.get("message"))
            if not sig:
                continue
            n_kept += 1
            key = (_source_key(e.get("source")), str(e.get("app") or "").lower(), sig)
            g = groups.get(key)
            if g is None:
                g = {"source": _source_key(e.get("source")), "app": e.get("app") or "", "signature": sig, "level": lv,
                     "count": 0, "hosts": [], "first": "", "last": "", "examples": []}
                groups[key] = g
                order.append(key)
            g["count"] += 1
            if LEVEL_RANK.get(lv, 9) < LEVEL_RANK.get(g["level"], 9):
                g["level"] = lv
            hn = e.get("host") or h.get("host") or ""
            if hn not in g["hosts"]:
                g["hosts"].append(hn)
            if t and (not g["first"] or t < g["first"]):
                g["first"] = t
            if t and t > g["last"]:
                g["last"] = t
            msg = e.get("message") or ""
            if len(g["examples"]) < nex and msg not in [x["message"] for x in g["examples"]]:
                g["examples"].append({"host": hn, "time": t, "message": msg})
        row = {k: v for k, v in h.items() if k not in ("entries", "ignore")}
        row["lines"] = len(h.get("entries") or [])
        row["kept"] = n_kept
        host_rows.append(row)
    pats = [groups[k] for k in order if groups[k]["count"] >= min_count]
    pats.sort(key=lambda g: (LEVEL_RANK.get(g["level"], 9), -len(g["hosts"]), -g["count"], g["source"], g["signature"]))
    probs = []
    for n, g in enumerate(pats, 1):
        where = g["hosts"][0] if len(g["hosts"]) == 1 else "%d hosts" % len(g["hosts"])
        probs.append({"id": "P%d" % n, "kind": "log", "severity": g["level"], "object_type": "host", "object": where,
                      "hosts": list(g["hosts"]), "source": g["source"], "app": g["app"],
                      "problem": g["examples"][0]["message"] if g["examples"] else g["signature"],
                      "signature": g["signature"],
                      "detail": "%d line(s) on %d host(s)%s%s" % (g["count"], len(g["hosts"]),
                                                                  ", first %s" % _t(g["first"]) if g["first"] else "",
                                                                  ", last %s" % _t(g["last"]) if g["last"] else ""),
                      "time": g["last"], "first": g["first"], "last": g["last"], "count": g["count"], "examples": g["examples"]})
    cap = int(o.get("max_items") or 30)
    for h in host_rows:
        h["patterns"] = sum(1 for g in pats if h.get("host") in g["hosts"])
    return {"hosts": host_rows, "patterns": len(pats), "all": probs, "problems": probs[:cap], "not_analyzed": probs[cap:],
            "lines": lines, "ignored": ignored, "outside": outside, "not_read": sum(1 for h in host_rows if _unread(h)),
            "critical": sum(1 for g in pats if g["level"] == "critical"),
            "error": sum(1 for g in pats if g["level"] == "error"),
            "warning": sum(1 for g in pats if g["level"] == "warning")}


def _where(p):
    hs = p.get("hosts") or []
    if len(hs) <= 3:
        return ", ".join(hs)
    return "%s and %d more" % (", ".join(hs[:3]), len(hs) - 3)


def _src(p):
    return "%s%s" % (p.get("source") or "?", " " + p["app"] if p.get("app") else "")


def _unread(h):
    return not h.get("reachable", True) or bool(h.get("error") and not h.get("lines"))


def log_evidence(data, problems, opts=None):
    """The evidence ACT reads: the window, which hosts were read and how, then each pattern with its
    counts, hosts and example lines - within opts.max_chars (60000)."""
    d, o = data or {}, opts or {}
    nl = "\n"
    lines = ["Logs of %s, read %s UTC." % (o.get("window") or "the last %s hours" % _hours(o.get("hours", 24)),
                                          _t(o.get("read_at")) or "now")]
    if o.get("question"):
        lines += ["", "THE OPERATIONS TEAM ASKS: " + " ".join(str(o["question"]).split())]
    lines += ["", "HOSTS READ (host, kind, lines)"]
    for h in d.get("hosts") or []:
        state = ("NOT READ: " + (h.get("error") or "not reached")) if _unread(h) else ""
        lines.append("%s: %s, %d line(s)%s" % (h.get("host", ""), SOURCE_LABEL.get(h.get("source"), h.get("source") or "?"),
                                                h.get("lines", 0), (" - " + state) if state else ""))
    lines += ["", "PATTERNS TO ANALYZE (the same line, numbers and names taken out, counted across hosts)"]
    for p in problems or []:
        lines.append("%s [%s] %s on %s - %s" % (p["id"], p["severity"].upper(), _src(p), _where(p), p.get("detail", "")))
        for ex in p.get("examples") or []:
            lines.append("    %s %s: %s" % (_t(ex.get("time")) or "-", ex.get("host", ""), ex.get("message", "")))
    text = nl.join(lines)
    cap = int(o.get("max_chars") or 60000)
    return text if len(text) <= cap else text[:cap] + nl + "[... evidence cut at %d characters ...]" % cap


def log_act_task(problems, opts=None):
    """The task (prompt) for ACT. opts: what, window, hours, question, extra."""
    o = opts or {}
    ids = ", ".join(p["id"] for p in problems or [])
    q = " ".join(str(o.get("question") or "").split())
    return "\n".join([
        "Log analysis for an operations team (%s)." % (o.get("what") or "servers"),
        "You cannot run any commands in this task, and none are needed: the evidence piped to you was read from the logs "
        "of %s (only the warning, error and critical lines, grouped: lines that differ only in numbers, times, addresses "
        "and names are one pattern, counted across hosts). Base every conclusion on that evidence and quote the lines that "
        "support it." % (o.get("window") or "the last %s hours" % _hours(o.get("hours", 24))),
    ] + (["", "The team asks: \"%s\". Answer that first in overall: which patterns relate to it, and whether the logs show "
              "its cause (say so plainly if they do not)." % q] if q else []) + [
        "",
        "Analyze each pattern in the PATTERNS TO ANALYZE list (%s). For each one give:" % ids,
        "- cause: the most likely cause, in one or two sentences - or that it is harmless noise, if it is;",
        "- evidence: the lines that support it;",
        "- fix: what an administrator should do, and what to check first. A person applies it; never claim you changed anything;",
        "- confidence: how sure you are that the cause is right, a whole number from 0 to 100: 80 or more = the evidence shows it; "
        "50-79 = likely, other causes possible; under 50 = a guess (then say in the fix what to check to be sure).",
        "Several patterns may share one cause (e.g. a storage path down shows in the kernel, the hypervisor agent and the "
        "applications; a certificate or a time problem breaks several services): say so in each of them, and put the shared "
        "cause first in overall. A pattern on many hosts at once points to something they share (network, DNS, time, storage, "
        "an account, a change).",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"overall": "two or three sentences: what to do first, and which patterns share a cause",',
        ' "items": [{"id": "P1", "cause": "...", "evidence": "...", "fix": "...", "confidence": 85}]}',
        MARK_END,
        "Include every id. Use plain text inside the JSON strings (no markdown).",
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def _conf_status(c):
    if c is None:
        return ""
    return "ok" if c >= 80 else "warning" if c >= 50 else "critical"


def _conf_text(c):
    if c is None:
        return ""
    return "%d%% %s" % (c, "high" if c >= 80 else "medium" if c >= 50 else "low")


def _capped(rows, n):
    n = int(n or 0)
    return rows if not n or len(rows) <= n else rows[:n]


def log_report(data, parsed=None, opts=None):
    """The report (roles/site_email layout). data: log_analysis's result. parsed: act_analysis_parse's.
    opts: title ('Log analysis'), what ('the hosts of web'), window (its label), hours, act (bool: ACT
    was asked for), ran (bool), reason (why ACT did not run or failed), raw (ACT's answer), provider,
    model, max_items, max_rows, question, settings (lines describing what was read)."""
    d, pa, o = data or {}, parsed or {}, opts or {}
    ps = d.get("problems") or []
    allp = d.get("all") or (ps + list(d.get("not_analyzed") or []))
    by = pa.get("by_id") or {}
    name = o.get("title") or "Log analysis"
    asked, ran = bool(o.get("act")) and bool(ps), bool(o.get("ran"))
    with_act = asked and ran and pa.get("parsed")
    hosts = d.get("hosts") or []
    unread = [h for h in hosts if _unread(h)]
    read = [h for h in hosts if not _unread(h)]
    n_pat = d.get("patterns", len(allp))
    window = o.get("window") or "the last %s h" % _hours(o.get("hours", 24))
    if not allp:
        title = "%s: no warning or error in the logs of %d host(s)" % (name, len(read))
    elif asked and not ran:
        title = "%s: ACT did not run - %d pattern(s) not analyzed" % (name, n_pat)
    elif asked and not pa.get("parsed"):
        title = "%s: %d pattern(s) - ACT's answer is below as text" % (name, n_pat)
    elif asked:
        title = "%s: likely causes and fixes for %d pattern(s)" % (name, len(ps))
    else:
        title = "%s: %d pattern(s) in the logs of %d host(s)" % (name, n_pat, len(read))
    if unread:
        title += ", %d host(s) not read" % len(unread)
    worst = "critical" if d.get("critical") or d.get("error") else "warning" if d.get("warning") else "ok"
    status = "critical" if (asked and not ran) or unread else (worst if allp else "ok")
    model = " ".join(x for x in (o.get("provider"), o.get("model")) if x)
    subtitle = ("The warning, error and critical log lines of %s on %s, grouped into patterns (the same line with other "
                "numbers, times and names is one pattern)." % (window, o.get("what") or "the hosts"))
    if with_act:
        subtitle += " ACT%s analyzed the top %d: likely causes, not certain ones - check before you change anything." % (
            " (" + model + ")" if model else "", len(ps))
    confs = [by[p["id"]]["confidence"] for p in ps if p["id"] in by and by[p["id"]].get("confidence") is not None]
    summary = [{"label": "Hosts read", "value": len(read)},
               {"label": "Not read", "value": len(unread), "status": "critical" if unread else "ok"},
               {"label": "Lines", "value": d.get("lines", 0)},
               {"label": "Patterns", "value": n_pat},
               {"label": "Critical", "value": d.get("critical", 0), "status": "critical" if d.get("critical") else "ok"},
               {"label": "Error", "value": d.get("error", 0), "status": "critical" if d.get("error") else "ok"},
               {"label": "Warning", "value": d.get("warning", 0), "status": "warning" if d.get("warning") else "ok"}]
    if with_act:
        summary += [{"label": "High confidence (80+)", "value": sum(1 for c in confs if c >= 80), "status": "ok"},
                    {"label": "Medium (50-79)", "value": sum(1 for c in confs if 50 <= c < 80), "status": "warning"},
                    {"label": "Low (under 50)", "value": sum(1 for c in confs if c < 50), "status": "critical"}]
    sections = []
    if unread:
        sections.append({"title": "Not read (%d)" % len(unread), "status": "critical", "columns": ["Host", "Kind", "Why"],
                         "rows": [[h.get("host", ""), SOURCE_LABEL.get(h.get("source"), h.get("source") or ""),
                                   h.get("error") or "not reached"] for h in unread],
                         "row_status": ["critical"] * len(unread), "cell_status": [["critical", "", ""]] * len(unread)})
    if o.get("question"):
        sections.append({"title": "Your question", "lines": [" ".join(str(o["question"]).split())]})
    if asked and not ran:
        sections.append({"title": "Why ACT did not run", "status": "critical",
                         "lines": [o.get("reason") or "unknown", "The patterns are listed below without an analysis."]})
    elif with_act and pa.get("overall"):
        sections.append({"title": "What to do first", "status": LEVEL_STATUS.get(ps[0]["severity"], ""),
                         "lines": pa.get("overall_lines") or [pa["overall"]]})     # asked in batches: a line per batch
    if with_act:
        rows, rs, cs = [], [], []
        for p in ps:
            a = by.get(p["id"]) or {}
            c = a.get("confidence")
            rows.append([p["id"], p["severity"].upper(), _where(p), p["problem"],
                         a.get("cause") or "ACT gave no answer for this one", a.get("evidence", ""), a.get("fix", ""), _conf_text(c)])
            rs.append(LEVEL_STATUS.get(p["severity"], ""))
            cs.append(["", LEVEL_STATUS.get(p["severity"], ""), "", "", "", "", "", _conf_status(c)])
        sections.append({"title": "Likely causes and fixes", "columns": ["#", "Level", "Hosts", "Pattern", "Likely cause", "Evidence",
                                                                          "Fix", "Confidence"],
                         "rows": rows, "row_status": rs, "cell_status": cs,
                         "text": "Confidence: ACT's own estimate that the cause is right - green 80+ (the evidence shows it), "
                                 "amber 50-79 (likely), red under 50 (a guess: check first)."})
    if asked and ran and not pa.get("parsed"):
        sections.append({"title": "ACT's answer", "text": pa.get("error", ""),
                         "lines": [x for x in str(o.get("raw") or "").splitlines() if x.strip()] or ["(empty)"]})
    if allp:
        shown = _capped(allp, o.get("max_rows"))
        rows = [[p["id"], p["severity"].upper(), _where(p), _src(p), p["count"], _t(p.get("first")) or "-", _t(p.get("last")) or "-",
                 p["problem"]] for p in shown]
        text = ("P%d to %s (over log_analysis_max_items, %s) were not given to ACT." % (len(ps) + 1, allp[-1]["id"], o.get("max_items", 30))
                if asked and len(allp) > len(ps) else "")
        if len(shown) < len(allp):
            text = (text + " " if text else "") + "%d more in the job artifacts (log_analysis_max_rows)." % (len(allp) - len(shown))
        sec = {"title": "Patterns (%d), most severe first" % len(allp),
               "columns": ["#", "Level", "Hosts", "Log", "Lines", "First seen", "Last seen", "Example"],
               "rows": rows, "row_status": [LEVEL_STATUS.get(p["severity"], "") for p in shown],
               "cell_status": [["", LEVEL_STATUS.get(p["severity"], ""), "", "", "", "", "", ""] for p in shown]}
        if text:
            sec["text"] = text
        sections.append(sec)
    if read:
        sections.append({"title": "Hosts read (%d)" % len(read), "columns": ["Host", "Kind", "Logs", "Lines", "Patterns"],
                         "rows": [[h.get("host", ""), SOURCE_LABEL.get(h.get("source"), h.get("source") or ""),
                                   ", ".join(s.get("name", "") + (" (" + s["error"] + ")" if s.get("error") else "")
                                             for s in h.get("sources") or [] if not s.get("skipped")) or "-",
                                   h.get("lines", 0), h.get("patterns", 0)] for h in _capped(read, o.get("max_rows"))]})
    settings = ["Window: %s." % window] + [str(x) for x in o.get("settings") or []]
    if d.get("ignored"):
        settings.append("%d line(s) left out by log_analysis_ignore." % d["ignored"])
    if d.get("outside"):
        settings.append("%d line(s) outside the window left out." % d["outside"])
    settings.append("ACT: " + ("%s%s." % ("ran" if ran else "did not run", " (" + model + ")" if model else "") if asked
                               else "not asked (log_analysis_act: false)." if not o.get("act") else "nothing to analyze."))
    sections.append({"title": "What was read", "lines": settings})
    for s in sections:
        if not s.get("text"):
            s.pop("text", None)
    footer = ("ACT is an AI assistant: it read the evidence only, ran nothing and changed nothing. Confidence is its own estimate."
              if with_act else "Read-only: nothing was changed on any host.")
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections, "footer": footer}


def log_names(data, extra=None):
    """Every host name the evidence may contain, and its short name, and extra (log_analysis_names_extra):
    ACT replaces them with placeholders before anything reaches the model."""
    out = set(_list(extra))
    for h in (data or {}).get("hosts") or []:
        n = str(h.get("host") or "")
        out.update([n, _short(n)])
        if h.get("vcenter"):
            out.update([str(h["vcenter"]), _short(h["vcenter"])])
    for p in (data or {}).get("all") or ((data or {}).get("problems") or []) + ((data or {}).get("not_analyzed") or []):
        for n in p.get("hosts") or []:
            out.update([str(n), _short(n)])
    return sorted(n for n in out if n and len(n) > 2 and "," not in n)


def log_host_wanted(name, patterns):
    """True when name matches one of the patterns (* ? allowed, any case); no patterns = every name."""
    pats = _list(patterns)
    if not pats:
        return True
    n = str(name or "").lower()
    return any(fnmatch.fnmatchcase(n, p.lower()) or fnmatch.fnmatchcase(_short(n), p.lower()) for p in pats)


def log_vcenter_unread(status):
    """A vCenter that could not be read (a site_vmware_* module's 'vcenters' entry) as a host row."""
    s = status or {}
    return {"host": s.get("name", ""), "source": "vcenter", "vcenter": s.get("name", ""), "reachable": False,
            "error": s.get("error", ""), "entries": [], "sources": []}


def log_list(value, lines_only=False):
    """A list setting as a list (a survey's 'a, b' or one per line)."""
    return _list(value, lines_only)


class FilterModule(object):
    def filters(self):
        return {"log_settings": log_settings, "log_window": log_window, "log_signature": log_signature, "log_level": log_level,
                "log_linux_entries": log_linux_entries, "log_win_entries": log_win_entries, "log_analysis": log_analysis,
                "log_evidence": log_evidence, "log_act_task": log_act_task, "log_report": log_report, "log_names": log_names,
                "log_host_wanted": log_host_wanted, "log_list": log_list, "log_vcenter_unread": log_vcenter_unread}
