# -*- coding: utf-8 -*-
"""1.77.0: the cross-check of the STIG answer keys (playbooks/stig_answer_keys.yml). For each rule with a key, four
sources side by side on each host - it never changes a block, it says which to read again:
  Evaluate-STIG   its own status without answer files: shown, not counted (a key is there because it is Open or Not
                  Reviewed - "Open, and the code says NF" is the key's purpose, not a disagreement)
  STIG Manager    the host's review, with its age: counted only when a person wrote it and it is newer than the scan
  the code        its result in the trial, where the trial ran; else "not tried"
  ACT, on its own DISA's check text and the host's read-only evidence for the rule (never the code): NF, Open or NA
Agree: two or more counted sources on a host, and no host where two differ. Need a look: two counted sources differ on
a host. Too little to compare: no host with two.

The code runs in the trial on one host per STIG (1.76.0), so that host has the code; a host whose STIG Manager review a
person wrote after the scan has that review. ACT is asked on those hosts only - where it has something to be compared
with - once per rule and evidence (hosts that return the same share one call), at most answer_keys_cross_check_act_max.

  stig_answer_cross_review   a STIG Manager review as the cross-check reads it (its word, age, counted or not)
  stig_answer_cross_plan     which rules each host reads evidence for (site_stig_answer_reads)
  stig_answer_cross_asks     the ACT calls: one per rule and evidence, capped
  stig_answer_cross_act_task what ACT is asked for a rule on a host (no code); stig_answer_cross_act_answer: its answer
  stig_answer_cross_join     one row per rule and host: Evaluate-STIG, STIG Manager, the code, ACT
  stig_answer_cross_rules    each rule: its hosts' four sources, folded, and the verdict
  stig_answer_cross_report   the sections, the tile and the lines; stig_answer_cross_into puts them in the report"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import os
import re

# 1.77.0b: the one redaction the host modules use (plugins/module_utils/site_redact.py; there: ansible.module_utils.site_redact),
# again here before a text goes into a page, workbook, email or ACT's task
_spec = importlib.util.spec_from_file_location(
    "site_redact", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_redact.py"))
_redact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_redact)
redact = _redact.redact
cap = _redact.cap

VERDICTS = ("NF", "Open", "NA")
SM_SHORT = {"pass": "NF", "fail": "Open", "notapplicable": "NA"}
AGREE, LOOK, THIN = "✓ Agree", "⚠ Need a look", "— Too little to compare"
ORDER = {LOOK: 0, THIN: 1, AGREE: 2}
ACT_MAX_DEFAULT = 50
CAP = "not asked (the cap, answer_keys_cross_check_act_max)"


def _s(x):
    return "" if x is None else str(x)


# 2026-11-17T10:00:00.900Z, ...+00:00, ...-0500: 1.77.0d (Codex) its fraction kept (to the microsecond) - .900Z is newer than .100Z
_TS = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:[.,](\d+))?(Z|[+-]\d\d:?\d\d)", re.I)


def _when(ts):
    m = _TS.fullmatch(_s(ts).strip())
    if not m:
        return None
    try:
        t = datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    z = m.group(3).upper()
    if z != "Z" and (int(z[1:3]) > 23 or int(z[-2:]) > 59):
        return None                           # an offset no clock has (+24:00, +05:60): not a time
    off = datetime.timedelta(0) if z == "Z" else (-1 if z[0] == "-" else 1) * datetime.timedelta(hours=int(z[1:3]), minutes=int(z[-2:]))
    return t.replace(microsecond=int((m.group(2) or "0")[:6].ljust(6, "0")), tzinfo=datetime.timezone(off))


def _days(a, b):
    return max(0, int((b - a).total_seconds() // 86400))


def stig_answer_cross_scan_time(sm):
    """A host's STIG Manager record (site_stigman_read) -> the time of its last scan there: the newest review a result
    engine wrote (the scan's import) - '' when none."""
    best = None
    for rv in ((sm or {}).get("reviews") or {}).values():
        t = _when(rv.get("ts")) if isinstance(rv, dict) and rv.get("engine") else None
        if t and (best is None or t > best):
            best = t
    if not best:
        return ""
    best = best.astimezone(datetime.timezone.utc)
    # 1.77.0d: with its fraction, when it has one (2026-11-12T10:00:00.9Z)
    return best.strftime("%Y-%m-%dT%H:%M:%S") + ((".%06d" % best.microsecond).rstrip("0") if best.microsecond else "") + "Z"


def stig_answer_cross_review(review, scan_time="", now=""):
    """review: site_stigman_read's {result, ts, engine} (engine: who wrote it - a result engine = the scan's import);
    scan_time: the asset's last scan in STIG Manager (stig_answer_cross_scan_time) -> {word, counted, short}: counted
    only when a person wrote it, both times are known and it is strictly newer than that scan. Otherwise it is shown and
    not counted: no scan time to compare, the same time as the scan, older than the scan, or its age unknown."""
    rv = review if isinstance(review, dict) else {}
    res = _s(rv.get("result"))
    if not res:
        return {"word": "none", "counted": False, "short": ""}
    short = SM_SHORT.get(res, "")
    # 1.77.0i (Codex P1): a result STIG Manager does not define is shown as a fixed word, never its text
    word = short or {"notchecked": "Not Reviewed", "informational": "Informational", "error": "Error", "fixed": "Fixed",
                     "unknown": "Unknown", "notselected": "Not Selected"}.get(res, "unknown result")
    ts, scan, at = _when(rv.get("ts")), _when(scan_time), _when(now) or datetime.datetime.now(datetime.timezone.utc)
    age = (" %d d" % _days(ts, at)) if ts else ""
    if rv.get("engine"):
        return {"word": "%s (from the scan,%s)" % (word, age or " age unknown"), "counted": False, "short": short}
    # 1.77.0b (Codex P2): counted only when both times are known and the review is strictly newer than the scan
    if ts is None:
        why = ""
    elif scan is None:
        why = " — no scan time to compare"
    elif ts == scan:
        why = " — same time as the scan"
    elif ts < scan:
        why = " — older than the scan"
    else:
        why = None
    counted = bool(short) and why is None
    note = "a person,%s%s" % (age or " age unknown", why or "")
    return {"word": "%s (%s)" % (word, note), "counted": counted, "short": short}


def stig_answer_cross_act_task(rule, evidence, values=None):
    """What ACT is asked for one rule on one host. rule: {v, title, check_text}; evidence: [{cmd, rc, out}] the job ran
    (read-only, the gate's allow-list) - never the code, its result or Evaluate-STIG's status."""
    r = rule if isinstance(rule, dict) else {}
    ev = []
    for e in evidence or []:
        ev.append("$ %s   (exit %s)\n%s" % (redact(_s(e.get("cmd")), backstop=False), _s(e.get("rc")), (redact(_s(e.get("out"))) or "(nothing)")[:4000]))
    vals = "\n".join("%s = %s" % (k, ", ".join(v) if isinstance(v, list) else v) for k, v in sorted((values or {}).items()))
    return ("You check one DISA STIG rule on one RHEL host, from evidence only. You run nothing.\n"
            "Rule %s: %s\n\nDISA's check text:\n%s\n\nWhat the host returned for these read-only commands:\n%s\n\n%s"
            "Answer on the first line with exactly one of NF (not a finding), Open, NA (not applicable) or unsure. "
            "On the second line, one short sentence: why, naming what in the evidence decides it."
            % (_s(r.get("v")), redact(_s(r.get("title")), backstop=False),
               redact(_s(r.get("check_text")), backstop=False).strip() or "(none)",
               "\n\n".join(ev) or "(none)", ("The site's values:\n" + vals + "\n\n") if vals else ""))


def stig_answer_cross_act_answer(text):
    """ACT's answer -> {verdict: NF / Open / NA / '' (unsure, or no answer), why}."""
    # 1.77.0d: ACT's answer may quote the evidence - redacted whole, then cut
    lines = [x.strip() for x in redact(_s(text)).strip().splitlines() if x.strip()]
    if not lines:
        return {"verdict": "", "why": "no answer"}
    first = re.sub(r"[^A-Za-z ]", " ", lines[0]).split()
    word = first[0].lower() if first else ""
    verdict = {"nf": "NF", "notafinding": "NF", "open": "Open", "na": "NA", "notapplicable": "NA"}.get(word, "")
    why = (lines[1] if len(lines) > 1 else "")[:300]
    return {"verdict": verdict, "why": why or ("unsure" if not verdict else "")}


def _fold(vals):
    """the values of a rule's hosts as one cell: "NF on 3", "NF 2 . Open 1"."""
    vs = [v for v in vals if v]
    if not vs:
        return "none"
    n = {}
    for v in vs:
        n[v] = n.get(v, 0) + 1
    if len(n) == 1:
        return "%s on %d" % (vs[0], len(vs)) if len(vs) > 1 else vs[0]
    return " · ".join("%s %d" % (k, c) for k, c in sorted(n.items(), key=lambda x: (-x[1], x[0])))


def stig_answer_cross_rules(rows):
    """rows: one per rule and host - {v, sv, cat, title, stig, host, es (Evaluate-STIG's word), sm (stig_answer_cross_review),
    code (NF / Open / NA / '' not tried), code_why ('' or why not tried), act ({verdict, why} or None, not asked),
    act_note, evidence (text), evidence_from (DISA's / the code's)} -> one per rule: {v, ..., verdict, why, cells, hosts}."""
    by = {}
    for r in rows or []:
        by.setdefault((_s(r.get("stig")), _s(r.get("v"))), []).append(r)
    out = []
    for (stig, v), hs in by.items():
        diffs, counted_any = [], 0
        for h in hs:
            src = []
            if (h.get("sm") or {}).get("counted"):
                src.append(("STIG Manager", h["sm"]["short"]))
            if h.get("code") in VERDICTS:
                src.append(("code", h["code"]))
            act = h.get("act") or {}
            if act.get("verdict") in VERDICTS:
                src.append(("ACT", act["verdict"]))
            counted_any = max(counted_any, len(src))
            if len(set(x[1] for x in src)) > 1:
                diffs.append("%s on %s" % (", ".join("%s %s" % x for x in src), _s(h.get("host")).split(".")[0]))
        if diffs:
            verdict, why = LOOK, " \u00b7 ".join(diffs[:2]) + (" (+%d hosts)" % (len(diffs) - 2) if len(diffs) > 2 else "")
        elif counted_any >= 2:
            verdict, why = AGREE, ""
        else:
            verdict, why = THIN, _thin_why(hs)
        f = hs[0]
        out.append({"v": v, "sv": _s(f.get("sv")), "cat": _s(f.get("cat")), "title": _s(f.get("title")), "stig": stig,
                    "verdict": verdict, "why": why, "hosts": hs,
                    "es": _fold([_s(h.get("es")) for h in hs]),
                    "sm": _fold([(h.get("sm") or {}).get("word", "none") for h in hs]),
                    "code": _fold([h.get("code") for h in hs if h.get("code")]) if any(h.get("code") for h in hs) else "not tried",
                    "act": _fold([(h.get("act") or {}).get("verdict") or "unsure" for h in hs if h.get("act")])
                    if any(h.get("act") for h in hs) else _act_none(hs)})
    out.sort(key=lambda r: (r["stig"], ORDER[r["verdict"]], r["v"]))
    return out


def _act_none(hs):
    notes = [_s(h.get("act_note")) for h in hs if h.get("act_note")]
    return "not asked (the cap)" if CAP in notes else "not asked"


def _compared(h):
    return bool(h.get("code") or h.get("act") or h.get("act_note") or (h.get("sm") or {}).get("counted"))


def _thin_why(hs):
    tried = any(h.get("code") in VERDICTS for h in hs)
    act = any((h.get("act") or {}).get("verdict") in VERDICTS for h in hs)
    if not tried and act:
        return "the code was not tried (%s) — ACT alone" % (redact(_s(hs[0].get("code_why"))) or "no trial")
    if tried and not act:
        return "ACT gave no answer (%s) — the code alone" % (_s(hs[0].get("act_note")) or "unsure")
    return "no source gave an answer"


def stig_answer_cross_report(rules, act_max=ACT_MAX_DEFAULT, ran=None, evidence="web"):
    """stig_answer_cross_rules' result -> {sections (web), mail (the email's), tile, title_part, lines, xlsx}. ran:
    [{host, cmd}] - every command the job ran on a host for the evidence (How this was made lists them all)."""
    rules = rules or []
    look = [r for r in rules if r["verdict"] == LOOK]
    agree = [r for r in rules if r["verdict"] == AGREE]
    thin = [r for r in rules if r["verdict"] == THIN]
    stigs = []
    for r in rules:
        if r["stig"] not in stigs:
            stigs.append(r["stig"])
    # the STIGs with rules that need a look first, then by name
    stigs.sort(key=lambda st: (-sum(1 for r in rules if r["stig"] == st and r["verdict"] == LOOK), st))
    by_stig = {"title": "Cross-check by STIG", "status": "warning" if look else "info",
               "text": "Four sources for each rule with a key. A rule agrees when two counted sources give the same answer on a host, and none differ anywhere.",
               "columns": ["STIG", "Rules", "Agree", "Need a look", "Too little to compare"], "kinds": ["wrap", "num", "num", "num", "num"],
               "rows": []}
    secs = [by_stig]
    for st in stigs:
        rs = [r for r in rules if r["stig"] == st]
        hosts = sorted(set(h["host"] for r in rs for h in r["hosts"]))
        n_look = sum(1 for r in rs if r["verdict"] == LOOK)
        by_stig["rows"].append(["%s (%s)" % (st, _n(len(hosts), "host")), len(rs), sum(1 for r in rs if r["verdict"] == AGREE), n_look,
                                sum(1 for r in rs if r["verdict"] == THIN)])
        secs.append({"title": "Cross-check — %s (%s%s)" % (st, _n(len(rs), "rule"), (", %d need a look" % n_look) if n_look else ""),
                     "status": "warning" if n_look else "info",
                     "text": "Disagreements first. It never changes a block: it tells you which to read again.",
                     "columns": ["", "Rule", "Cross-check", "Evaluate-STIG", "STIG Manager", "The code (trial)", "ACT, on its own"],
                     "kinds": ["x", "", "wrap", "wrap", "wrap", "wrap", "wrap"],
                     "rows": [["", "%s %s" % (r["v"], r["cat"]) if r["cat"] else r["v"], r["verdict"] + ((": " + r["why"]) if r["why"] else ""),
                               r["es"], r["sm"], r["code"], r["act"]] for r in rs],
                     "details": [_detail(r, evidence) for r in rs], "keep_case": True, "wrap": True})
    mail = [by_stig, {"title": "Need a look in the cross-check (%d)" % len(look), "status": "warning" if look else "ok",
                      "columns": ["Rule", "Cross-check", "The code", "ACT, on its own"],
                      "rows": [["%s (%s)" % (r["v"], r["stig"]), r["why"], r["code"], r["act"]] for r in look], "wrap": True}]
    tile = {"label": "Cross-check", "value": "%d need a look" % len(look), "status": "warning" if look else "ok",
            "note": "%d agree, %d too little to compare" % (len(agree), len(thin))}
    how = ["The cross-check puts four sources side by side. It never changes a block.",
           "Evaluate-STIG: its own status, without answer files. Shown, not counted: a key is there because it is Open or Not Reviewed.",
           "STIG Manager: the host's review, with its age. Counted only when a person wrote it and it is newer than the scan.",
           "The code: its result in the trial, where the trial ran. Else not tried.",
           "ACT, on its own: DISA's check text and the host's read-only evidence, never the code. At most %d calls a run "
           "(answer_keys_cross_check_act_max). Past it: %s." % (int(act_max), CAP),
           "The evidence: DISA's commands that fit the read-only list, else the code's own reads. Each a literal command, "
           "checked against the read-only list and run once by the job (never by ACT), on the trial host and where a person "
           "reviewed the rule after the scan.",
           "Agree: two counted sources give the same answer on a host, and none differ anywhere. Need a look: two counted "
           "sources differ on a host. Too little to compare: no host has two."]
    # 1.77.0i (Codex P1): the commands as the host expanded them (a file name a glob matched) - the website and the job's
    # output only; the email (and none) says how many
    how_mail = how + (["The job ran %s for the evidence. The website lists them." % _n(len(ran), "read-only command")] if ran else [])
    how = how_mail if evidence == "none" else how + ["Ran on %s: %s" % (_s(x.get("host")), _s(x.get("cmd"))) for x in ran or []]
    # 1.77.0h: the workbook goes with the email - ACT's why (it may quote the evidence) on the website only
    xlsx = {"name": "Cross-check", "columns": ["Rule", "STIG", "Host", "Cross-check", "Evaluate-STIG", "STIG Manager", "The code",
                                                "ACT, on its own", "Evidence from"],
            "rows": [["%s (%s)" % (r["v"], r["sv"]), r["stig"], h["host"], r["verdict"], _s(h.get("es")), (h.get("sm") or {}).get("word", ""),
                      h.get("code") or "not tried", (h.get("act") or {}).get("verdict") or ("unsure" if h.get("act") else
                                                                                   _s(h.get("act_note_mail") or h.get("act_note")) or "not asked"),
                      _s(h.get("evidence_from"))] for r in rules for h in r["hosts"]]}
    lines = ["Cross-check: %d agree, %d need a look, %d too little to compare" % (len(agree), len(look), len(thin))] + \
        ["%s CROSS-CHECK: %s" % (r["v"], r["why"]) for r in look]
    return {"sections": secs, "mail": mail, "tile": tile, "look": len(look), "how": how, "how_mail": how_mail, "xlsx": xlsx, "lines": lines,
            "words": dict((r["v"], r["verdict"]) for r in rules),
            "words_by": dict(("%s|%s" % (r["stig"], r["v"]), r["verdict"]) for r in rules)}


def _n(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _detail(r, evidence="web"):
    d = [{"columns": ["What it checks", ""], "kinds": ["wrap", "wrap"], "rows": [[r["title"], r["stig"]]]},
         {"columns": ["Host", "Evaluate-STIG", "STIG Manager", "The code", "ACT, on its own", "ACT's why"],
          "kinds": ["wrap"] * 6,
          "rows": [[h["host"], _s(h.get("es")), (h.get("sm") or {}).get("word", "none"), h.get("code") or "not tried",
                    (h.get("act") or {}).get("verdict") or ("unsure" if h.get("act") else _s(h.get("act_note")) or "not asked"),
                    (h.get("act") or {}).get("why", "")] for h in r["hosts"] if _compared(h)]}]
    rest = [h for h in r["hosts"] if not _compared(h)]
    if rest:
        d[1]["rows"].append(["%s more" % _n(len(rest), "host"), _fold([_s(h.get("es")) for h in rest]), "nothing newer than the scan",
                             "tried on one host only", "not asked", "nothing to compare it with there"])
    ev = "\n".join("%s - %s" % (h["host"], _s(h.get("evidence"))) for h in r["hosts"] if h.get("evidence"))
    if ev and evidence != "none":             # 1.77.0h: at most 20 lines and 2000 characters a rule; none: not shown
        d.append({"texts": [{"label": "What ACT was given (read-only, collected by the job; ACT never saw the code)", "text": cap(ev),
                             "note": "DISA's check text was given too. Evidence from: %s." % _fold([_s(h.get("evidence_from")) for h in r["hosts"]])}]})
    return d

# ---- the job's side: which host reads what, the ACT calls, the rows ----------------------------------------------------
TRUE_SHORT = {"NotAFinding": "NF", "Not_Applicable": "NA", "Open": "Open"}


def _ready(g):
    return [r for r in g.get("rows") or [] if r.get("block")]


def _sv(r):
    return _s(r.get("sv")) or _s(r.get("v"))


def stig_answer_cross_plan(groups, info, now="", act_on=True):
    """groups: the report's (answer_keys_report.yml _ak_groups: rows, rep, hosts); info: {host: {sm: its STIG Manager
    record, es: {V-: Evaluate-STIG's word}}} -> {host: [{id, check_text, calls}]}: the rules with a block, read on the
    trial host (it has the code) and on each host whose review a person wrote after the scan (it has STIG Manager).
    ACT off: nothing is read (nothing would be asked)."""
    out = {}
    if not act_on:
        return out
    for g in groups or []:
        rep = _s(g.get("rep"))
        for r in _ready(g):
            calls = [c for c in ((r.get("gate") or {}).get("calls") or []) if isinstance(c, list)]
            item = {"id": _s(r.get("v")), "check_text": _s(r.get("check_text")), "calls": calls}
            for h in r.get("hosts") or [rep]:
                hi = (info or {}).get(h) or {}
                sm = hi.get("sm") or {}
                rv = ((sm.get("reviews") or {}) if sm.get("status") == "found" else {}).get(_sv(r))
                counted = stig_answer_cross_review(rv, stig_answer_cross_scan_time(sm), now)["counted"] if rv else False
                if h == rep or counted:
                    out.setdefault(h, []).append(item)
    return out


def _ev_text(ran):
    return "\n\n".join("$ %s   (exit %s)\n%s" % (redact(_s(e.get("cmd")), backstop=False), _s(e.get("rc")), (redact(_s(e.get("out"))).rstrip() or "(nothing)")[:4000])
                       for e in ran or [])


def stig_answer_cross_asks(groups, reads, act_max=ACT_MAX_DEFAULT, act_on=True, act_off_why=""):
    """reads: {host: site_stig_answer_reads' reads ({V-: {from, ran, why}}) or {'failed': why}} -> {asks: [{id, v, label,
    task, evidence, hosts}], who: {'label|V-|host': ask id, or why ACT is not asked there}}. One call per rule and
    evidence: hosts that returned the same share it. At most act_max calls; past it: CAP."""
    asks, who, seen, n, mail = [], {}, {}, 0, {}
    most = max(0, int(act_max if act_max is not None else ACT_MAX_DEFAULT))
    for g in groups or []:
        label = _s(g.get("label"))
        for r in _ready(g):
            for h in r.get("hosts") or [g.get("rep")]:
                k = "%s|%s|%s" % (label, r["v"], h)
                hr = (reads or {}).get(h)
                if hr is None:
                    continue                 # nothing to compare with on this host: not read, not asked
                if not act_on:
                    who[k] = act_off_why or "ACT is off"
                    continue
                if hr.get("failed"):
                    who[k] = "not asked: the reads failed on the host (%s)" % redact(_s(hr["failed"]))[:200]
                    mail[k] = "not asked: the reads failed"     # 1.77.0i (Codex P1): the workbook's, without the host's words
                    continue
                x = (hr.get("reads") or {}).get(r["v"]) or {}
                if not x.get("ran"):
                    who[k] = "not asked: no read-only evidence (%s)" % (redact(_s(x.get("why"))) or "not read")
                    mail[k] = "not asked: no read-only evidence"
                    continue
                ev = _ev_text(x["ran"])
                sk = (label, r["v"], ev)
                if sk in seen:
                    seen[sk]["hosts"].append(h)
                    who[k] = seen[sk]["id"]
                    continue
                if n >= most:
                    who[k] = CAP
                    continue
                n += 1
                a = {"id": "x%d" % n, "v": r["v"], "label": label, "hosts": [h], "evidence": ev, "from": _s(x.get("from")),
                     "task": stig_answer_cross_act_task({"v": r["v"], "title": r.get("title"), "check_text": r.get("check_text")},
                                                        x["ran"], r.get("uses") or {})}
                asks.append(a)
                seen[sk] = a
                who[k] = a["id"]
    return {"asks": asks, "who": who, "who_mail": mail}


def stig_answer_cross_join(groups, info, reads, asks, answers, now=""):
    """One row per rule with a block and host it was found on (stig_answer_cross_rules' input). answers: {ask id: ACT's
    raw answer text}."""
    rows, who, who_mail = [], (asks or {}).get("who") or {}, (asks or {}).get("who_mail") or {}
    by_id = dict((a["id"], a) for a in (asks or {}).get("asks") or [])
    for g in groups or []:
        label, rep = _s(g.get("label")), _s(g.get("rep"))
        for r in _ready(g):
            tr = r.get("trial") or {}
            for h in r.get("hosts") or [rep]:
                hi = (info or {}).get(h) or {}
                sm = hi.get("sm") or {}
                rv = ((sm.get("reviews") or {}) if sm.get("status") == "found" else {}).get(_sv(r))
                if h == rep and tr.get("valid") is True:
                    code, code_why = "NF", ""
                elif h == rep and tr.get("valid") is False:
                    code, code_why = "Open", ""
                else:
                    code, code_why = "", ("tried on %s only" % rep.split(".")[0]) if h != rep else "not tried"
                k, act, note = "%s|%s|%s" % (label, r["v"], h), None, ""
                w = who.get(k)
                if w in by_id:
                    act = stig_answer_cross_act_answer((answers or {}).get(w))
                elif w:
                    note = w
                hr = ((reads or {}).get(h) or {}).get("reads") or {}
                x = hr.get(r["v"]) or {}
                rows.append({"v": r["v"], "sv": _s(r.get("sv")), "cat": _s(r.get("cat")), "title": _s(r.get("title")), "stig": label,
                             "host": h, "es": _s((hi.get("es") or {}).get(r["v"])),
                             "sm": stig_answer_cross_review(rv, stig_answer_cross_scan_time(sm), now) if sm else
                             {"word": "not read", "counted": False, "short": ""},
                             "code": code, "code_why": code_why, "act": act, "act_note": note,
                             "act_note_mail": who_mail.get(k, note) if note else "",
                             "evidence": _ev_text(x.get("ran")) if act else "", "evidence_from": _s(x.get("from")) if act else ""})
    return rows


def stig_answer_cross_ran(reads):
    """reads: {host: {reads: {V-: {ran}}}} -> [{host, cmd}]: every command the job ran for the evidence, the hosts that
    ran the same one together."""
    by, order = {}, []
    for h in sorted(reads or {}):
        for v, x in sorted(((reads[h] or {}).get("reads") or {}).items()):
            for e in x.get("ran") or []:
                c = redact(_s(e.get("cmd")), backstop=False)    # 1.77.0h: as the evidence shows it (a value it quotes)
                if c not in by:
                    by[c] = []
                    order.append(c)
                if h not in by[c]:
                    by[c].append(h)
    return [{"host": ", ".join(by[c]), "cmd": c} for c in order]


def stig_answer_cross_into(doc, cross):
    """1.76.0's report (stig_answer_report_multi) + the cross-check (stig_answer_cross_report): the tile first, the
    cross-check sections before the blocks, a Cross-check column in each STIG's "answer keys to copy" (web), the email's
    two tables, a workbook sheet, its How this was made lines."""
    if not cross or not (cross.get("words") is not None):
        return doc
    look, words = cross.get("look") or 0, cross.get("words_by") or {}

    def one(d, secs_extra, web):
        d = dict(d)
        if look:
            d["title"] = d["title"].replace(" ready to copy, ", " ready to copy, %d need a look in the cross-check, " % look, 1)
            if d.get("status") in ("ok", "info"):
                d["status"] = "warning"
        d["summary"] = [cross["tile"]] + list(d.get("summary") or [])
        secs = []
        for sec in d.get("sections") or []:
            t = _s(sec.get("title"))
            if web and " - answer keys to copy (" in t and sec.get("rows"):
                lab = t.split(" - answer keys to copy (")[0]
                cols = list(sec["columns"])
                i = cols.index("Rule") + 1 if "Rule" in cols else 1
                cols.insert(i, "Cross-check")
                kinds = list(sec.get("kinds") or [])
                if kinds:
                    kinds.insert(i, "wrap")
                rows = []
                for row in sec["rows"]:
                    v = _s(row[i - 1]).split(" (")[0]
                    rows.append(list(row[:i]) + [words.get("%s|%s" % (lab, v), "")] + list(row[i:]))
                sec = dict(sec, columns=cols, rows=rows, **({"kinds": kinds} if kinds else {}))
            if t == "How this was made":
                sec = dict(sec, lines=list(sec.get("lines") or []) + (cross["how"] if web else cross.get("how_mail") or cross["how"]))
            secs.append(sec)
        d["sections"] = secs_extra + secs
        return d

    out = dict(doc)
    out["web"] = one(doc["web"], cross["sections"], True)
    out["report"] = one(doc["report"], cross["mail"], False)
    out["xlsx"] = [cross["xlsx"]] + list(doc.get("xlsx") or [])
    out["lines"] = list(doc.get("lines") or []) + cross["lines"]
    return out



class FilterModule(object):
    def filters(self):
        return {"stig_answer_cross_review": stig_answer_cross_review, "stig_answer_cross_act_task": stig_answer_cross_act_task,
                "stig_answer_cross_act_answer": stig_answer_cross_act_answer, "stig_answer_cross_rules": stig_answer_cross_rules,
                "stig_answer_cross_report": stig_answer_cross_report, "stig_answer_cross_scan_time": stig_answer_cross_scan_time,
                "stig_answer_cross_plan": stig_answer_cross_plan, "stig_answer_cross_asks": stig_answer_cross_asks,
                "stig_answer_cross_join": stig_answer_cross_join, "stig_answer_cross_ran": stig_answer_cross_ran,
                "stig_answer_cross_into": stig_answer_cross_into}
