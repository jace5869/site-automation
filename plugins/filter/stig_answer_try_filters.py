# -*- coding: utf-8 -*-
"""1.77.0: STIG answer keys - try my answer file (playbooks/stig_answer_keys_try.yml). Evaluate-STIG runs twice on each
test host, for the picked rules only: without answer files, then with the site's (check_stig_evaluate_answers). For each
rule and host: the status without and with, the AnswerKey that applied, its comment, whether it changed the result, and
what its code read when Evaluate-STIG kept it. Nothing is written anywhere.

  stig_answer_try_af      what an answer file did to one rule, from the checklist's comment and finding details
  stig_answer_try_rows    each rule on each host: the two runs side by side, and what happened
  stig_answer_try_report  the report, its web copy and the workbook"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

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
EVIDENCE_OFF = _redact.EVIDENCE_OFF

WORD = {"fail": "Open", "pass": "Not a Finding", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed"}
CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
GOOD = ("pass", "notapplicable")
# what Evaluate-STIG writes in a rule an answer file answered (its comment):
#   Evaluate-STIG answer file for Key 'DEFAULT' is changing the Status from Open to NotAFinding and providing the below
#   comment on 10/30/2026:
#
#   <the AnswerKey's comment>
AF_RE = re.compile(r"answer\s*file\s+for\s+key\s*'([^']+)'(.*?)(?:\r?\n\s*\r?\n|$)", re.I | re.S)
CHANGE_RE = re.compile(r"changing\s+the\s+status\s+from\s+(\S+)\s+to\s+(\S+)", re.I)
# 1.77.0: where Evaluate-STIG keeps what ValidationCode said (Write-Verbose) is not known for every version: a block that
# a line naming ValidationCode starts, up to a separator line - else the report says it was not kept
VERBOSE_RE = re.compile(r"^.*validation\s*code.*$", re.I | re.M)
SEP_RE = re.compile(r"^\s*(?:-{4,}|~{4,}|={4,})\s*$", re.M)
NOT_KEPT = "Evaluate-STIG did not keep what the code read"
LOOK = "⚠ "
OK = "✓ "


def _s(x):
    return "" if x is None else str(x)


def _verbose(text):
    m = VERBOSE_RE.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    sep = SEP_RE.search(rest)
    out = (rest[:sep.start()] if sep else rest).strip()
    return out[:2000]


def stig_answer_try_af(item):
    """One rule's item of the run with answer files (site_stig_scan rule_texts) -> {key, from, to, comment, verbose}:
    key '' = no answer file answered it."""
    it = item if isinstance(item, dict) else {}
    com, det = redact(_s(it.get("comments"))), redact(_s(it.get("details")))
    out = {"key": "", "from": "", "to": "", "comment": "", "verbose": ""}
    for text in (com, det):
        m = AF_RE.search(text)
        if not m:
            continue
        out["key"] = m.group(1).strip()
        ch = CHANGE_RE.search(m.group(0))
        if ch:
            out["from"], out["to"] = ch.group(1).strip(".,:"), ch.group(2).strip(".,:")
        after = text[m.end():].strip()
        sep = SEP_RE.search(after)
        out["comment"] = (after[:sep.start()] if sep else after).strip()[:1500]
        break
    out["verbose"] = _verbose(det) or _verbose(com)
    return out


def stig_answer_try_rows(runs, rules=None):
    """runs: [{host, without: site_stig_scan's result (no answer files), with: its result with them}]; rules: the V ids asked
    -> [{host, v, sv, title, cat, without, with, key, comment, verbose, changed, what, group}] - group: look (a key that
    left it Open or Not Reviewed, no key on a rule still Open, not in a run), changed (a key set it Not a Finding or Not
    Applicable), passed (no key needed: Evaluate-STIG passes it on its own); and [{host, why}] for a host not tried."""
    rows, not_run = [], []
    want = [_s(v) for v in rules or []]
    for r in runs or []:
        host = _s(r.get("host"))
        a, b = r.get("without") or {}, r.get("with") or {}
        # 1.77.0d (Codex): Evaluate-STIG's own words when it wrote no checklist - redacted here too (site_stig_scan did on the host)
        # 1.77.0i (Codex P1): a task that failed or a host not reached (no_log hides its raw result) - its msg, redacted
        why = "" if a.get("scanned") else ("without answer files: " + (redact(_s(a.get("why") or a.get("msg"))) or "it did not run"))
        why = why or ("" if b.get("scanned") else ("with your answer files: " + (redact(_s(b.get("why") or b.get("msg"))) or "it did not run")))
        if why:
            # 1.77.0h: the email's reason without Evaluate-STIG's own words
            not_run.append({"host": host, "why": why, "why_mail": "Evaluate-STIG did not run %s." % (
                "without answer files" if not a.get("scanned") else "with your answer files")})
            continue
        ia = dict((_s(i.get("v")), i) for i in a.get("items") or [] if isinstance(i, dict))
        ib = dict((_s(i.get("v")), i) for i in b.get("items") or [] if isinstance(i, dict))
        for v in want or sorted(set(ia) | set(ib)):
            x, y = ia.get(v), ib.get(v)
            it = y or x or {}
            row = {"host": host, "v": v, "sv": _s(it.get("key")), "title": _s(it.get("title")),
                   "cat": CAT.get(_s(it.get("severity")), ""), "without": WORD.get(_s((x or {}).get("result")), "not in the run"),
                   "with": WORD.get(_s((y or {}).get("result")), "not in the run"),
                   "key": "", "comment": "", "verbose": "", "changed": False, "what": "", "group": "look"}
            if not x or not y:
                row["what"] = LOOK + "Not in the %s run (not in the STIG Evaluate-STIG checked here)" % (
                    "first" if not x else "second")
                rows.append(row)
                continue
            af = stig_answer_try_af(y)
            row.update(key=af["key"], comment=af["comment"], verbose=af["verbose"] or (NOT_KEPT if af["key"] else ""),
                       changed=x.get("result") != y.get("result"))
            good = y.get("result") in GOOD
            if af["key"] and good:
                row.update(group="changed", what=OK + ("Changed by the key" if row["changed"] else
                                                      "The key applied (it was %s already)" % row["with"]))
            elif af["key"]:
                row["what"] = LOOK + "The key applied and set it %s" % row["with"]
            elif good:
                row.update(group="passed", what=OK + "No key needed: Evaluate-STIG says %s on its own" % row["with"])
            else:
                row["what"] = LOOK + "No key applied: %s" % row["with"]
            rows.append(row)
    order = {"look": 0, "changed": 1, "passed": 2}
    rows.sort(key=lambda z: (order[z["group"]], z["v"], z["host"]))
    return {"rows": rows, "not_run": not_run}


def _n(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def stig_answer_try_report(tried, info=None, evidence="web"):
    """tried: stig_answer_try_rows' result; info: {key, source, files [{name, sha256, modified}], stig, ran [{host, cmd}]} -> {report (the email), web, xlsx, lines}."""
    info = info or {}
    rows, not_run = tried.get("rows") or [], tried.get("not_run") or []

    def shown(text):                          # 1.77.0h: answer_keys_evidence - web: capped; none: not at all
        return EVIDENCE_OFF if evidence == "none" else cap(text)
    look = [r for r in rows if r["group"] == "look"]
    changed = [r for r in rows if r["group"] == "changed"]
    passed = [r for r in rows if r["group"] == "passed"]
    n_hosts = len(set(r["host"] for r in rows)) + len(not_run)
    title = "Try my answer file: %d changed by a key, %d need a look" % (len(changed), len(look))
    status = "warning" if look or not_run else ("ok" if changed else "info")
    tiles = [{"label": "Changed by a key", "value": len(changed), "status": "ok" if changed else "info"},
             {"label": "Need a look", "value": len(look), "status": "warning" if look else "ok",
              "note": "a key left it Open, or none applied" if look else ""},
             {"label": "Hosts", "value": n_hosts, "status": "warning" if not_run else "info",
              "note": ("%d not tried (why: Not tried, below)" % len(not_run)) if not_run else _s(info.get("stig"))},
             {"label": "Written", "value": "nothing", "status": "info", "note": "STIG Manager, the share, the host"}]
    cols = ["Rule", "Host", "What it checks", "Without answer files", "With them", "What happened"]

    def cells(r):
        return ["%s %s" % (r["v"], r["cat"]) if r["cat"] else r["v"], r["host"], r["title"], r["without"], r["with"], r["what"]]

    # 1.77.0i (Codex P1): the key is read from the checklist's comment - shown only as the key this job gave (or
    # DEFAULT); another name in that comment is "another key", never its text
    known = dict((k.lower(), k) for k in (_s(info.get("key")) or "DEFAULT", "DEFAULT"))

    def key_of(r):
        return (known.get(r["key"].lower()) or "another key") if r["key"] else ""

    def detail(r):
        d = [{"columns": ["", ""], "kinds": ["wrap", "wrap"],
              "rows": [["AnswerKey applied", key_of(r) or "none"], ["Changed the result", "yes" if r["changed"] else "no"],
                       ["Its comment (as Evaluate-STIG wrote it)", shown(r["comment"]) if r["comment"] else "none"]]}]
        if r["key"]:
            d.append({"texts": [{"label": "What the ValidationCode read (Write-Verbose)",
                                 "text": shown(r["verbose"]) if r["verbose"] and r["verbose"] != NOT_KEPT else NOT_KEPT, "note": ""}]})
        return d

    def sec(name, rs, st, text, web=True):
        # the website counts a section's rows beside its title itself: the count in the title is for the email
        s = {"title": name if web else "%s (%d)" % (name, len(rs)), "status": st, "text": text, "columns": ([""] if web else []) + cols,
             "rows": [([""] if web else []) + cells(r) for r in rs], "keep_case": True, "wrap": True}
        if web:
            s.update(kinds=["x", "", "wrap", "wrap", "wrap", "wrap", "wrap"], details=[detail(r) for r in rs])
        return s

    look_text = ("A key that applied but left the rule Open: read what its code read. A rule no key applied to keeps "
                 "Evaluate-STIG's own status.")
    files = {"title": "Your answer files", "status": "info", "columns": ["File", "sha256", "Changed"],
             "kinds": ["wrap", "wrap", "wrap"],
             "rows": [[_s(f.get("name")), _s(f.get("sha256")), _s(f.get("modified"))] for f in info.get("files") or []]}
    nr = {"title": "Not tried", "status": "warning", "columns": ["Host", "Why"], "kinds": ["wrap", "wrap"],
          "rows": [[x["host"], x.get("why_mail") or x["why"] if evidence == "none" else x["why"]] for x in not_run]}
    nr_mail = dict(nr, rows=[[x["host"], x.get("why_mail") or x["why"]] for x in not_run])
    ran = ["%s: %s" % (_s(x.get("host")), _s(x.get("cmd"))) for x in info.get("ran") or []]
    how = {"title": "How this was made", "collapsed": True, "lines": [
        "Evaluate-STIG ran twice on each host, for the picked rules only (--SelectVuln): first without answer files, then with yours.",
        "Your answer files: check_stig_evaluate_answers (%d files, from %s). Each file's name and sha256 are listed above." % (
            len(info.get("files") or []), _s(info.get("source")) or "?"),
        "They were copied to a folder of this run on each host (root only, mode 0700) and removed after. The host keeps nothing.",
        "The status, the AnswerKey and its comment come from the checklist Evaluate-STIG wrote. Its own output folder was removed.",
        "What the code read: shown when Evaluate-STIG keeps it in the checklist. Else: " + NOT_KEPT + ".",
        "Of check_stig_evaluate_args only its --AnswerKey was given (no --Output). Nothing was written to STIG Manager or the share."]
        + (["Ran: " + x for x in ran])}

    def build(web):
        secs = []
        if not_run:
            secs.append(nr if web else dict(nr_mail, title="Not tried (%d)" % len(not_run)))
        secs.append(sec("Need a look", look, "warning" if look else "ok", look_text, web))
        secs.append(sec("Changed by your answer file", changed, "ok", "Open a rule for the comment and what its code read." if web else "", web))
        if passed:
            secs.append(sec("No key needed", passed, "info", "Evaluate-STIG passes these on its own.", web))
        return secs + [files, how]

    base = {"title": title, "status": status, "summary": tiles,
            "subtitle": "%s, %s, %s, answer key %s. A trial: nothing written." % (
                _s(info.get("stig")) or "?", _n(n_hosts, "host"), _n(len(set(r["v"] for r in rows)), "rule"),
                _s(info.get("key")) or "DEFAULT")}
    # 1.77.0h: the workbook goes with the email - no host's text in it (the comment and what the code read: the website)
    xlsx = [{"name": "Try my answer file", "columns": ["Rule", "Host", "What it checks", "Without", "With", "AnswerKey",
                                                       "Changed", "What happened"],
             "rows": [["%s (%s)" % (r["v"], r["sv"]), r["host"], r["title"], r["without"], r["with"], key_of(r),
                       "yes" if r["changed"] else "no", r["what"]] for r in rows]}]
    lines = [title] + ["%s %s: %s -> %s, %s" % (r["host"], r["v"], r["without"], r["with"], r["what"]) for r in rows] + \
        ["%s NOT TRIED: %s" % (x["host"], x["why"]) for x in not_run]
    return {"report": dict(base, sections=build(False)), "web": dict(base, sections=build(True)), "xlsx": xlsx, "lines": lines}


class FilterModule(object):
    def filters(self):
        return {"stig_answer_try_af": stig_answer_try_af, "stig_answer_try_rows": stig_answer_try_rows,
                "stig_answer_try_report": stig_answer_try_report}
