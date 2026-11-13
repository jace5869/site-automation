# -*- coding: utf-8 -*-
"""Filters for the Horizon FSLogix profiles report (playbooks/horizon_fslogix_report.yml): what
roles/horizon/library/site_fslogix_scan.py listed on the profile shares (folder and file names, sizes, times -
no container is ever opened) made into one record per container, and every judgement of the report.

What FSLogix's documentation says (learn.microsoft.com/en-us/fslogix/):
  - reference-configuration-settings: a user's folder is %sid%_%username% (SIDDirNamePattern), or
    %username%_%sid% with FlipFlopProfileDirectoryName; the profile container is Profile_%username%
    (VHDNamePattern), the ODFC container ODFC_%username%; .vhd or .vhdx (VolumeType); SizeInMBs (30000) is the
    most a container may hold.
  - concepts-multi-concurrent-connections: with ProfileType 3 the RW differencing disk RW.VHD(x) is kept next
    to the profile container until it is merged (the RO ones are local); an ODFC differencing disk on the share
    is %computername%_ODFC.VHD(x); session ODFC disks are ODFC-%username%-SESSION-<0-9>.VHD(x).
  - concepts-fslogix-cloud-cache: Cloud Cache keeps a lock file and a meta file next to the container on
    each storage provider (the prerequisites page names *.lock, *.meta, *.metadata).
What this report infers (and says so): a container is "likely in use" when it, or its lock file, was written
in the last few minutes; a differencing disk not written for hours is one left behind (a merge not done); the
file's size is how much of SizeInMBs the container has taken - a dynamic disk does not shrink when files in it
are deleted, until FSLogix compacts it at sign-out (concepts-vhd-disk-compaction), and a fixed disk is always
its full size. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import importlib.util
import os
import re
import time
from datetime import datetime, timezone

# the times a report shows, in report_time_zone (1.54.0): plugins/module_utils/site_time.py, as site_filters.py loads it
_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)

MB = 1024 * 1024
GB = 1024 * MB
SID = r"(?P<sid>S-1-\d+(?:-\d+)+)"
# the FSLogix variables a name pattern may hold (any other %name% matches any text)
VARS = {"%sid%": SID, "%username%": r"(?P<user>.+?)", "%userdomain%": r"(?P<domain>[^_\\/]+?)"}
EXT = r"\.(?P<ext>vhdx?)"
RW_RE = re.compile(r"^RW" + EXT + "$", re.I)                                    # documented (ProfileType 3)
ODFC_DIFF_RE = re.compile(r"^(?P<computer>.+)_ODFC" + EXT + "$", re.I)           # documented (VHDAccessMode)
ODFC_SESSION_RE = re.compile(r"^ODFC-(?P<user>.+)-SESSION-(?P<n>\d+)" + EXT + "$", re.I)   # documented
VHD_RE = re.compile(EXT + "$", re.I)
AUX = (".lock", ".meta", ".metadata")                                           # Cloud Cache's (documented)
SEV = {"critical": 0, "warning": 1, "low": 2, "info": 3}   # 1.56.0 low: worth knowing, nothing to do now


def _pattern(tpl, ext=""):
    """An FSLogix name pattern (Profile_%username%, %sid%_%username%) -> an anchored regular expression, any case."""
    out, seen = [], set()
    for part in re.split(r"(%\w+%)", str(tpl or "")):
        low = part.lower()
        if low in VARS and low not in seen:
            out.append(VARS[low])
            seen.add(low)
        elif re.match(r"^%\w+%$", part):
            out.append(r".+?")
        else:
            out.append(re.escape(part))
    return re.compile("^" + "".join(out) + ext + "$", re.I)


def _now(now):
    """now: seconds (or milliseconds) since 1970, or '' for the clock."""
    try:
        n = float(now)
    except (TypeError, ValueError):
        return time.time()
    if not n:
        return time.time()
    return n / 1000.0 if n > 1e11 else n


def _when(t, tz=None):
    """Seconds -> '2026-11-01 22:00 UTC'; with tz (report_time_zone, 1.54.0) as that zone shows it, '2026-11-01 23:00 CET'."""
    try:
        return _tm.local(datetime.fromtimestamp(float(t), timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), tz) if t else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _size(n):
    n = float(n or 0)
    return "%.1f GB" % (n / GB) if n >= GB / 10 else "%.0f MB" % (n / MB)


def _ago(seconds):
    s = max(0, int(seconds))
    if s < 3600:
        return "%d min" % (s // 60)
    if s < 2 * 86400:
        return "%d h" % (s // 3600)
    return "%d days" % (s // 86400)


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _list(v):
    if v in (None, ""):
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def hz_fslogix_names(opts=None):
    """The patterns the report looks for -> {folder, profile, odfc} (regular expressions, as text, for the job output)."""
    o = opts or {}
    return {"folder": _pattern(o.get("folder_pattern") or "%sid%_%username%").pattern,
            "profile": _pattern(o.get("profile_name") or "Profile_%username%", EXT).pattern,
            "odfc": _pattern(o.get("odfc_name") or "ODFC_%username%", EXT).pattern}


def hz_fslogix_file(name, profile_re, odfc_re):
    """One file of a user folder -> (what, match): profile, odfc, odfc session, rw diff, odfc diff, aux, vhd (a VHD(x) of
    another name) or other."""
    if RW_RE.match(name):
        return "rw diff", RW_RE.match(name)
    for what, rx in (("profile", profile_re), ("odfc", odfc_re), ("odfc session", ODFC_SESSION_RE)):
        m = rx.match(name)
        if m:
            return what, m
    m = ODFC_DIFF_RE.match(name)
    if m:
        return "odfc diff", m
    low = name.lower()
    if low.endswith(AUX):
        return "aux", None
    if VHD_RE.search(name):
        return "vhd", None
    return "other", None


def _folder_user(name, folder_re):
    m = folder_re.match(name or "")
    if not m:
        return None
    g = m.groupdict()
    return {"sid": g.get("sid") or "", "user": g.get("user") or "", "domain": g.get("domain") or ""}


def _flag(rec, sev, code, text):
    rec["flags"].append({"severity": sev, "code": code, "text": text})


def hz_fslogix(scan, opts=None):
    """The report -> {report, containers, folders, shares, critical, warning, counts}. scan: site_fslogix_scan's result
    ({shares: [{path, error, total, free, folders: [{name, error, files: [{name, size, modified}], dirs}], files}]}).
    opts: size_mb, odfc_size_mb (0 = size_mb), warn_pct, crit_pct, stale_days, share_warn_pct, share_crit_pct,
    in_use_minutes, diff_left_hours, folder_pattern, profile_name, odfc_name, ignore (file names to pass over),
    max_rows, now."""
    o = opts or {}
    now = _now(o.get("now"))
    cap = {"profile": _num(o.get("size_mb"), 30000) * MB}
    cap["odfc"] = (_num(o.get("odfc_size_mb"), 0) * MB) or cap["profile"]
    cap["odfc session"] = cap["odfc"]
    warn_pct, crit_pct = _num(o.get("warn_pct"), 85), _num(o.get("crit_pct"), 95)
    stale_days = _num(o.get("stale_days"), 90)
    sw_pct, sc_pct = _num(o.get("share_warn_pct"), 15), _num(o.get("share_crit_pct"), 5)
    in_use_s = _num(o.get("in_use_minutes"), 30) * 60
    left_s = _num(o.get("diff_left_hours"), 24) * 3600
    mx = int(_num(o.get("max_rows"), 50))
    ignore = [x.lower() for x in _list(o.get("ignore"))]
    folder_tpl = o.get("folder_pattern") or "%sid%_%username%"
    folder_re = _pattern(folder_tpl)
    # the forms FSLogix makes by itself, to still name the user of a folder outside folder_tpl (said as naming)
    known = [(t, _pattern(t)) for t in ("%sid%_%username%", "%username%_%sid%") if t != folder_tpl]
    profile_re = _pattern(o.get("profile_name") or "Profile_%username%", EXT)
    odfc_re = _pattern(o.get("odfc_name") or "ODFC_%username%", EXT)

    containers, folders, shares = [], [], []
    for sh in (scan or {}).get("shares") or []:
        path = sh.get("path") or ""
        srec = {"path": path, "error": sh.get("error") or "", "total": sh.get("total"), "free": sh.get("free"),
                "containers": 0, "used": 0, "growth": 0, "status": "ok", "why": "", "truncated": bool(sh.get("truncated"))}
        shares.append(srec)
        if srec["error"]:
            srec["status"], srec["why"] = "critical", "not read: " + srec["error"]
            continue
        groups = [{"name": "", "error": "", "files": sh.get("files") or [], "dirs": [], "top": True}] + list(sh.get("folders") or [])
        for fo in groups:
            top = fo.get("top", False)
            fname = fo.get("name") or ""
            who = None if top else _folder_user(fname, folder_re)
            other = None
            if not top and who is None:
                other = next(((t, _folder_user(fname, rx)) for t, rx in known if _folder_user(fname, rx)), None)
            seen = who or (other[1] if other else {})
            frec = {"share": path, "folder": fname, "sid": seen.get("sid", ""), "user": seen.get("user", ""),
                    "files": [], "containers": 0, "flags": [], "error": fo.get("error") or ""}
            mine, diffs, aux, odd = [], [], [], []
            for f in fo.get("files") or []:
                n = str(f.get("name") or "")
                if not n or any(fnmatch.fnmatchcase(n.lower(), p) for p in ignore):
                    continue
                frec["files"].append(n)
                what, m = hz_fslogix_file(n, profile_re, odfc_re)
                rec = {"name": n, "size": int(_num(f.get("size"), 0)), "modified": _num(f.get("modified"), 0)}
                if what in ("profile", "odfc", "odfc session"):
                    g = m.groupdict()
                    mine.append(dict(rec, kind=what, user=g.get("user") or "", ext=(g.get("ext") or "").lower()))
                elif what in ("rw diff", "odfc diff"):
                    diffs.append(dict(rec, kind=what))
                elif what == "aux":
                    aux.append(rec)
                elif what == "vhd":
                    odd.append(rec)
            if top:
                if not mine and not odd:
                    continue            # files beside the user folders that are no container: not FSLogix's
                for c in mine + odd:
                    _flag(frec, "warning", "naming", "%s is in the share's own folder, not in a user's folder" % c["name"])
            folders.append(frec)
            if not top:
                if frec["error"]:
                    _flag(frec, "warning", "unread", "the folder could not be read: %s" % frec["error"])
                if who is None:
                    _flag(frec, "warning", "naming", "the folder's name is not %s%s" % (folder_tpl, (
                        " - it is %s%s: FSLogix set as now will not find it" % (
                            other[0], " (FlipFlopProfileDirectoryName)" if other[0].startswith("%username%") else "")) if other else ""))
                if not mine and not frec["error"]:
                    _flag(frec, "low", "no_container",
                          "no container in it - " + ("empty" if not frec["files"] and not fo.get("dirs") else
                                                     "only " + ", ".join((frec["files"] + ["%s\\" % d for d in fo.get("dirs") or []])[:6])))
            for x in ([] if top else odd):
                _flag(frec, "warning", "naming", "%s is a VHD(x) of another name (not %s, %s or a differencing disk)"
                      % (x["name"], o.get("profile_name") or "Profile_%username%", o.get("odfc_name") or "ODFC_%username%"))
            here = []                   # this folder's containers (not a search of every share's)
            for c in mine:
                srec["containers"] += 1
                srec["used"] += c["size"]
                c_cap = cap[c["kind"]]
                srec["growth"] += max(0, c_cap - c["size"])
                rec = {"share": path, "folder": fname, "file": c["name"], "user": c["user"] or frec["user"],
                       "folder_user": frec["user"], "sid": frec["sid"], "kind": c["kind"], "ext": c["ext"], "size": c["size"],
                       "cap": int(c_cap), "pct": round(100.0 * c["size"] / c_cap, 1) if c_cap else 0.0,
                       "modified": c["modified"], "diffs": [], "aux": [], "in_use": "", "in_use_why": "", "flags": []}
                stem = c["name"].lower()
                rec["aux"] = [a for a in aux if a["name"].lower().startswith(stem)]
                own = "rw diff" if c["kind"] == "profile" else ("odfc diff" if c["kind"] == "odfc" else "")
                rec["diffs"] = [d for d in diffs if d["kind"] == own]
                containers.append(rec)
                here.append(rec)
                frec["containers"] += 1
            # lock and meta files of no container, differencing disks of none
            named = set(a["name"] for c in here for a in c["aux"] + c["diffs"])
            for x in diffs + aux:
                if x["name"] not in named and mine:
                    _flag(frec, "low", "other", "%s belongs to no container here" % x["name"])

    # each container judged
    for c in containers:
        newest = max([c["modified"]] + [d["modified"] for d in c["diffs"]] + [a["modified"] for a in c["aux"]])
        c["last"] = newest
        age = now - newest if newest else None
        if c["pct"] >= crit_pct:
            _flag(c, "critical", "full", "%s%% of the cap (%s of %s)%s" % (
                c["pct"], _size(c["size"]), _size(c["cap"]), " - at or over it" if c["pct"] >= 100 else ""))
        elif c["pct"] >= warn_pct:
            _flag(c, "warning", "near", "%s%% of the cap (%s of %s)" % (c["pct"], _size(c["size"]), _size(c["cap"])))
        if c["folder_user"] and c["user"] and c["user"].lower() != c["folder_user"].lower():
            _flag(c, "warning", "naming", "%s is not the folder's user (%s)" % (c["file"], c["folder_user"]))
        if c["diffs"]:
            d_new = max(d["modified"] for d in c["diffs"])
            names = ", ".join("%s (%s)" % (d["name"], _size(d["size"])) for d in c["diffs"])
            if d_new and now - d_new <= left_s:
                c["in_use"] = "yes"
                c["in_use_why"] = "%s written %s ago - a session's differencing disk" % (names, _ago(now - d_new))
            else:
                c["in_use"] = "left?"
                c["in_use_why"] = "%s not written for %s" % (names, _ago(now - d_new) if d_new else "a time not known")
                _flag(c, "warning", "diff_left", "%s - a differencing disk left behind, a merge not done? (an inference: "
                      "look for a session of %s before you touch it)" % (c["in_use_why"], c["user"] or "the user"))
        if not c["in_use"] and age is not None and age <= in_use_s:
            lock = [a["name"] for a in c["aux"] if a["name"].lower().endswith(".lock")]
            c["in_use"] = "likely"
            c["in_use_why"] = "written %s ago%s (an inference)" % (_ago(age), (" - Cloud Cache lock file " + lock[0]) if lock else "")
        if age is not None and age >= stale_days * 86400:
            _flag(c, "low", "stale", "not written for %s - %s to reclaim once you know %s has left" % (
                _ago(age), _size(c["size"] + sum(d["size"] for d in c["diffs"])), c["user"] or "the user"))

    # two containers of one kind for one user (by SID, by name)
    keys = {}
    for i, c in enumerate(containers):
        if c["kind"] not in ("profile", "odfc"):
            continue
        if c["sid"]:
            keys.setdefault(("sid", c["kind"], c["sid"].lower()), []).append(i)
        if c["user"]:
            keys.setdefault(("user", c["kind"], c["user"].lower()), []).append(i)
    others = {}
    for k, idx in keys.items():
        if len(idx) > 1:
            for i in idx:
                others.setdefault(i, set()).update(j for j in idx if j != i)
    for i, js in sorted(others.items()):
        c = containers[i]
        sids = set(x["sid"].lower() for x in [c] + [containers[j] for j in js] if x["sid"])
        where = ", ".join(sorted("%s\\%s" % (containers[j]["folder"] or "(the share)", containers[j]["file"]) for j in js))
        _flag(c, "warning", "duplicate", "another %s container for %s: %s%s" % (
            c["kind"], c["user"] or c["sid"], where,
            " - another SID: the account may have been made again" if len(sids) > 1 else
            (" - in the same folder (.vhd and .vhdx?)" if any(containers[j]["folder"] == c["folder"] and containers[j]["share"] == c["share"]
                                                           for j in js) else "")))

    # the shares
    for s in shares:
        if s["error"]:
            continue
        if s["total"] and s["free"] is not None:
            s["free_pct"] = round(100.0 * s["free"] / s["total"], 1)
            if s["free_pct"] < sc_pct:
                s["status"], s["why"] = "critical", "%s%% free - below %g%%" % (s["free_pct"], sc_pct)
            elif s["free_pct"] < sw_pct:
                s["status"], s["why"] = "warning", "%s%% free - below %g%%" % (s["free_pct"], sw_pct)
            if s["growth"] > s["free"]:
                s["why"] = (s["why"] + "; " if s["why"] else "") + "the containers could still grow by %s, more than is free" % _size(s["growth"])
        else:
            s["free_pct"] = None
            s["status"], s["why"] = "warning", "free space not known (the share did not say)"
        if s["truncated"]:
            s["status"] = "warning" if s["status"] == "ok" else s["status"]
            s["why"] = (s["why"] + "; " if s["why"] else "") + "more folders than horizon_fslogix_max_folders: not all read"

    return _report(containers, folders, shares, o, {"warn": warn_pct, "crit": crit_pct, "stale": stale_days, "mx": mx, "now": now,
                                                     "tz": _tm.zone(o.get("time_zone"))[0]})


def _has(rec, code):
    return any(f["code"] == code for f in rec["flags"])


def _texts(rec, codes=None):
    return "; ".join(f["text"] for f in sorted(rec["flags"], key=lambda f: SEV[f["severity"]])
                     if codes is None or f["code"] in codes)


def _row(c, tz=None):
    return [c["user"] or "?", c["sid"], c["kind"], _size(c["size"]), "%s%%" % c["pct"], _when(c["last"], tz),
            (c["in_use"] + (" - " + c["in_use_why"] if c["in_use_why"] else "")) if c["in_use"] else "",
            "%s\\%s" % (c["folder"] or "(the share)", c["file"])]


def _report(containers, folders, shares, o, t):
    mx, tz = t["mx"], t.get("tz")
    worst = lambda r: min([SEV[f["severity"]] for f in r["flags"]] or [9])   # noqa: E731
    full = sorted([c for c in containers if _has(c, "full") or _has(c, "near")], key=lambda c: -c["pct"])
    stale = sorted([c for c in containers if _has(c, "stale")], key=lambda c: c["last"] or 0)
    dup = sorted([c for c in containers if _has(c, "duplicate")], key=lambda c: ((c["user"] or "").lower(), c["folder"]))
    left = [c for c in containers if _has(c, "diff_left")]
    naming = [("folder", f) for f in folders if _has(f, "naming")] + [("container", c) for c in containers if _has(c, "naming")]
    empty = [f for f in folders if _has(f, "no_container") or _has(f, "unread") or _has(f, "other")]
    in_use = [c for c in containers if c["in_use"] in ("yes", "likely")]
    n_dup = len(set((c["kind"], (c["user"] or c["sid"]).lower()) for c in dup))
    reclaim = sum(c["size"] + sum(d["size"] for d in c["diffs"]) for c in stale)
    nc = len([c for c in containers if worst(c) == 0]) + len([s for s in shares if s["status"] == "critical"])
    nw = (len([c for c in containers if worst(c) == 1]) + len([f for f in folders if worst(f) == 1])
          + len([s for s in shares if s["status"] == "warning"]))
    probs = []
    for s in shares:
        if s["status"] != "ok":
            probs.append((s["status"], "%s: %s" % (s["path"], s["why"])))
    over = [c for c in containers if _has(c, "full")]
    if over:
        probs.append(("critical", "%d container(s) at %g%% of the cap or more - those users get errors when it fills: %s" % (
            len(over), t["crit"], ", ".join("%s (%s, %s%%)" % (c["user"] or "?", c["kind"], c["pct"]) for c in over[:10]))))
    near = [c for c in full if not _has(c, "full")]
    if near:
        probs.append(("warning", "%d container(s) at %g%% of the cap or more" % (len(near), t["warn"])))
    if dup:
        probs.append(("warning", "%d user(s) with two containers of one kind - FSLogix uses one; the other is space, or a lost profile"
                      % n_dup))
    if naming:
        probs.append(("warning", "%d folder(s) or container(s) outside the expected naming - FSLogix will not find them" % len(naming)))
    if left:
        probs.append(("warning", "%d differencing disk(s) left behind (not written for %g h)" % (len(left), _num(o.get("diff_left_hours"), 24))))
    status = "critical" if nc else ("warning" if nw else "ok")
    n_users = len(set((c["user"] or c["sid"]).lower() for c in containers))
    share_rows, share_st = [], []
    for s in shares:
        share_rows.append([s["path"], s["containers"], _size(s["used"]),
                           "" if s["error"] or s.get("free") is None else _size(s["free"]),
                           "" if s.get("free_pct") is None else "%s%%" % s["free_pct"],
                           "" if s["error"] else _size(s["growth"]), s["why"] or "ok"])
        share_st.append(s["status"] if s["status"] != "ok" else "")
    biggest = sorted(containers, key=lambda c: -c["size"])
    title = "Horizon FSLogix profiles: %s" % (
        ", ".join(x for x in ["%d over or near the cap" % len(full) if full else "",
                              "%d share(s) to look at" % len([s for s in shares if s["status"] != "ok"]) if any(s["status"] != "ok" for s in shares) else "",
                              "%d stale" % len(stale) if stale else ""] if x)
        or "%d container(s), all within limits" % len(containers))
    sections = ([{"title": "Problems", "lines": [p[1] for p in sorted(probs, key=lambda p: SEV[p[0]])],
                  "status": "critical" if nc else "warning"}] if probs else []) + [
        {"title": "Shares", "columns": ["Share", "Containers", "Containers take", "Free", "Free %", "Could still grow", "Status"],
         "rows": share_rows, "row_status": share_st,
         "text": "Free space as the file server reports it. Could still grow: what the containers may take before each reaches its cap."},
        {"title": "Near or over the cap", "columns": ["User", "SID", "Kind", "Size", "% of cap", "Last written", "In use", "File"],
         "rows": [_row(c, tz) for c in full[:mx]], "row_status": ["critical" if _has(c, "full") else "warning" for c in full[:mx]],
         "status": "critical" if over else ("warning" if full else "ok"),
         "text": ("Size is the container file's size against the cap (SizeInMBs: %s profile, %s ODFC). A dynamic disk does not shrink "
                  "when files in it are deleted until FSLogix compacts it at sign-out; a fixed disk is always 100%%. Amber at %g%%, red at %g%%."
                  % (_size(_num(o.get("size_mb"), 30000) * MB), _size((_num(o.get("odfc_size_mb"), 0) or _num(o.get("size_mb"), 30000)) * MB),
                     t["warn"], t["crit"]))},
        {"title": "Two containers for one user", "columns": ["User", "SID", "Kind", "Size", "Last written", "File", "Why"],
         "rows": [[c["user"] or "?", c["sid"], c["kind"], _size(c["size"]), _when(c["last"], tz), "%s\\%s" % (c["folder"] or "(the share)", c["file"]),
                   _texts(c, ("duplicate",))] for c in dup[:mx]],
         "status": "warning" if dup else "ok",
         "text": "Two folders for one SID or one name (FlipFlopProfileDirectoryName changed, an account made again), or a .vhd "
                 "and a .vhdx in one folder. FSLogix uses one; the newest is usually the one in use."},
        {"title": "Outside the expected naming", "columns": ["What", "Folder", "Why"],
         "rows": [[k, r["folder"] or "(the share)", _texts(r, ("naming",))] for k, r in naming[:mx]],
         "status": "warning" if naming else "ok",
         "text": "Folders: %s. Containers: %s or %s (.vhd or .vhdx), and the documented differencing disks." % (
             o.get("folder_pattern") or "%sid%_%username%", o.get("profile_name") or "Profile_%username%", o.get("odfc_name") or "ODFC_%username%")},
        {"title": "Differencing disks left behind", "columns": ["User", "SID", "File", "Why"],
         "rows": [[c["user"] or "?", c["sid"], "%s\\%s" % (c["folder"], c["file"]), _texts(c, ("diff_left",))] for c in left[:mx]],
         "status": "warning" if left else "ok"},
        # 1.56.0: stale containers and leftover folders/files are Low (worth knowing, nothing to do now) - the word in the text, the tile and the Why column
        dict({"title": "Stale - space to reclaim", "columns": ["User", "SID", "Kind", "Size", "Last written", "File"],
         "rows": [[c["user"] or "?", c["sid"], c["kind"], _size(c["size"]), _when(c["last"], tz), "%s\\%s" % (c["folder"] or "(the share)", c["file"])]
                  for c in stale[:mx]],
         "text": "Not written for %g days or more: %s in all. Check in AD that the user has left before anyone removes one - this job removes nothing."
                 % (t["stale"], _size(reclaim))}, **({"status": "low", "row_status": ["low"] * len(stale[:mx]),
                 "text": "Low: worth knowing, nothing to do now. Not written for %g days or more: %s in all. Check in AD that the user "
                         "has left before anyone removes one - this job removes nothing." % (t["stale"], _size(reclaim))} if stale else {})),
        dict({"title": "Folders with no container, other files", "columns": ["Folder", "Why"],
              "rows": [[f["folder"] or "(the share)", ("" if _has(f, "unread") else "Low - ") + _texts(f, ("no_container", "unread", "other"))]
                       for f in empty[:mx]]},
             **({"row_status": ["" if _has(f, "unread") else "low" for f in empty[:mx]]} if any(not _has(f, "unread") for f in empty) else {})),
        {"title": "Largest containers", "columns": ["User", "SID", "Kind", "Size", "% of cap", "Last written", "In use", "File"],
         "rows": [_row(c, tz) for c in biggest[:min(mx, 20)]],
         "text": "In use: yes = a differencing disk written lately (Microsoft documents RW.VHDX and %%computername%%_ODFC.VHDX next to the "
                 "container while it is used); likely = the container or its lock file written in the last %g minutes (an inference)."
                 % _num(o.get("in_use_minutes"), 30)}]
    report = {"title": title,
              "subtitle": "%d container(s) of %d user(s) in %d folder(s) on %d share(s); read from directory listings only - no container opened"
                          % (len(containers), n_users, len([f for f in folders if f["folder"]]), len(shares)),
              "status": status,
              "summary": [{"label": "Containers", "value": len(containers)},
                          {"label": "Over the cap", "value": len(over), "status": "critical" if over else "ok"},
                          {"label": "Near the cap", "value": len(near), "status": "warning" if near else "ok"},
                          {"label": "Two for one user", "value": n_dup, "status": "warning" if dup else "ok"},
                          {"label": "Naming", "value": len(naming), "status": "warning" if naming else "ok"},
                          dict({"label": "Stale", "value": len(stale), "note": _size(reclaim) + " to reclaim"},
                               **({"status": "low", "note": "Low - %s to reclaim" % _size(reclaim)} if stale else {})),
                          {"label": "In use", "value": len(in_use)},
                          {"label": "Shares low", "value": len([s for s in shares if s["status"] != "ok"]),
                           "status": "critical" if any(s["status"] == "critical" for s in shares) else
                           ("warning" if any(s["status"] == "warning" for s in shares) else "ok")}],
              "sections": sections}
    flagged = [{"user": c["user"], "sid": c["sid"], "kind": c["kind"], "path": "%s\\%s\\%s" % (c["share"], c["folder"], c["file"]) if c["folder"]
                else "%s\\%s" % (c["share"], c["file"]), "size": c["size"], "pct": c["pct"], "last": _when(c["last"]), "in_use": c["in_use"],
                "flags": [f["code"] for f in c["flags"]]} for c in containers if c["flags"]]
    return {"report": report, "critical": nc, "warning": nw, "containers": containers, "folders": folders, "shares": shares,
            "flagged": flagged,
            "counts": {"containers": len(containers), "users": n_users, "over": len(over), "near": len(near), "duplicate": n_dup,
                       "naming": len(naming), "stale": len(stale), "reclaim_bytes": reclaim, "diff_left": len(left), "in_use": len(in_use),
                       "no_container": len([f for f in folders if _has(f, "no_container")]),
                       "shares_low": len([s for s in shares if s["status"] != "ok"])}}


def hz_fslogix_read_lines(scan):
    """What was read, for the job output: one line per share."""
    out = []
    for s in (scan or {}).get("shares") or []:
        if s.get("error"):
            out.append("NOT READ - %s: %s" % (s.get("path"), s.get("error")))
        else:
            out.append("%s%s: %d folder(s), %d file(s)%s" % (
                s.get("path"), (" as " + scan["account"]) if scan.get("account") else "", len(s.get("folders") or []),
                sum(len(f.get("files") or []) for f in s.get("folders") or []) + len(s.get("files") or []),
                ", %s free of %s" % (_size(s["free"]), _size(s["total"])) if s.get("total") else ", free space not known"))
    return out


def hz_fslogix_shares(value):
    """horizon_fslogix_shares as a list: a list, or a survey's text (one per line, or separated by ;)."""
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[\r\n;]+", value) if x.strip()]
    return [str(x).strip() for x in value or [] if str(x).strip()]


class FilterModule(object):
    def filters(self):
        return {"hz_fslogix": hz_fslogix, "hz_fslogix_shares": hz_fslogix_shares, "hz_fslogix_names": hz_fslogix_names, "hz_fslogix_read_lines": hz_fslogix_read_lines}
