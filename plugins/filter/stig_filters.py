# -*- coding: utf-8 -*-
"""STIG drift (roles/check_stig, playbooks/stig_drift_report.yml): a host's STIG scan (site_stig_scan) compared with
its reviews in STIG Manager (site_stigman_read) and with the host's earlier scans kept on the host.

  drift        the scan fails a rule STIG Manager has as Not a Finding (or Not Applicable), and the rule passed on
               this host in an earlier scan: the host moved away from its checklist - a finding (CAT I critical,
               CAT II / III warning: check_stig_severity), every run until it is fixed or its review changes
  new          the scan fails a rule that passed in an earlier scan (and STIG Manager does not have it as Open, nor
               as Not a Finding - that is drift) - a finding
  disagree     the scan fails a rule STIG Manager has as Not a Finding, and it has failed in every scan of this host:
               nothing moved that this job saw - the host was like this before its first scan, or the scanner checks the
               rule another way than DISA's check text (OpenSCAP has its own checks). Report only (check_stig_disagree_severity)
  fixed_open   the scan passes a rule STIG Manager still has Open: the checklist is behind - report only
  unreviewed   the scan fails a rule STIG Manager has no review for - report only
  ignored      a rule in check_stig_ignore_rules (the scanner is known to get it wrong at this site) that fails: listed,
               never a finding
  by_hand      the rules STIG Manager has a review of that the scan gives no result for (DISA's manual checks)
STIG Manager is never written to here: stig_update_plan only lists what workflow 37 writes after an approval
(playbooks/stigman_update.yml)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os
import re

CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SCAN_WORD = {"pass": "pass", "fail": "FAIL", "notapplicable": "n/a", "notchecked": "not checked", "error": "error",
             "unknown": "unknown", "informational": "info", "fixed": "fixed"}
SM_WORD = {"pass": "Not a Finding", "fail": "Open", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed",
           "informational": "Informational"}
# the chip of a cell: the scan's result, STIG Manager's
SCAN_CHIP = {"pass": "ok", "fail": "critical", "notapplicable": "info", "error": "warning", "unknown": "unknown", "notchecked": "unknown"}
SM_CHIP = {"pass": "ok", "notapplicable": "info", "fail": "warning", "notchecked": "unknown", "informational": "info"}
DEFAULT_SEVERITY = {"high": "critical", "medium": "warning", "low": "warning"}
STIGMAN_ACCEPT = ["saved", "submitted", "accepted"]
CHECKED = ("pass", "fail", "notapplicable")      # a result of the scan's own: the rest is left to a person
RULE_RE = re.compile(r"^S?V-(\d+)", re.I)


# Evaluate-STIG's own lines around what it found: "Evaluate-STIG 1.2607.0 (Scan-RHEL8_Checks) found this to be OPEN on
# 10/30/2026", "ResultHash: <hex>", and rules of ~ or -
_ES_HEAD_RE = re.compile(r"Evaluate-STIG\s+[\d.]+\s*(\([^)]*\))?\s*found this to be\s+\S+(\s+on\s+\S+)?", re.I)
_ES_HASH_RE = re.compile(r"ResultHash:\s*[0-9A-Fa-f]{16,}")
_ES_RULE_RE = re.compile(r"^\s*[~=_*-]{3,}\s*$")
SHORT_DETAILS = 160


def details_text(text):
    """What Evaluate-STIG found, without its own header (version, date, hash) and separator rules."""
    t = _ES_HASH_RE.sub("", _ES_HEAD_RE.sub("", text or ""))
    lines = [ln.rstrip() for ln in t.replace("\r\n", "\n").split("\n")]
    lines = [("" if _ES_RULE_RE.match(ln) else re.sub(r"\s+[~=_-]{5,}$", "", ln)) for ln in lines]
    out = "\n".join(lines)
    out = re.sub(r"[ \t]*~{3,}[ \t]*", " ", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _details_short(r):
    """The table cell: the first of what was found, and where the rest is."""
    t = details_text(r.get("details"))
    one = re.sub(r"\s+", " ", t)
    if len(one) <= SHORT_DETAILS:
        return one
    return one[:SHORT_DETAILS].rsplit(" ", 1)[0] + " ... (in full: What Evaluate-STIG found, below)"


def _who(c):
    """A result's name in the report: the host, or - a host with several STIGs (Windows) - the host and the STIG."""
    return c.get("label") or c["host"]


def _rule_label(it):
    return it.get("v") or it.get("key", "") if not it.get("stigid") else "%s (%s)" % (it.get("v") or it["key"], it["stigid"])


def _title(it, smr):
    return (smr or {}).get("title") or it.get("title") or it.get("key", "")


def _worst(items, sev_map):
    sevs = [sev_map.get(i.get("severity"), "warning") for i in items]
    return "critical" if "critical" in sevs else "warning"


def _short_list(items, n=5):
    xs = ["%s %s" % (i.get("v") or i["key"], CAT.get(i.get("severity"), i.get("severity", ""))) for i in items[:n]]
    return ", ".join(xs) + (" and %d more" % (len(items) - n) if len(items) > n else "")


def _plural(n, one, many):
    return one if n == 1 else many


def ignore_set(rules):
    """check_stig_ignore_rules -> the set to match against: SV-257985, V-257985 or SV-257985r1069364_rule -> SV-257985;
    a STIG id (RHEL-09-255045) or a SCAP rule name (sshd_disable_root_login) in lower case. A text: split on commas."""
    if isinstance(rules, str):
        rules = re.split(r"[,\s]+", rules)
    out = set()
    for x in rules or []:
        t = str(x).strip()
        m = RULE_RE.match(t)
        if m:
            out.add("SV-" + m.group(1))
        elif t:
            out.add(t.lower())
    return out


def _ignored(rec, ign):
    if not ign:
        return False
    return (rec["key"] in ign or rec["key"].lower() in ign or (rec.get("stigid") or "").lower() in ign
            or any(s.lower() in ign for s in rec.get("scap") or []))


def check_command(scan, rec):
    """The command that checks this one rule again on the host, as the scan did (its SCAP rule, profile, datastream).
    Evaluate-STIG: none - what it found is the rule's details."""
    ids = rec.get("scap_ids") or []
    ds = (scan or {}).get("datastream") or ""
    if not ids or not ds:
        return ""
    tail = (" --tailoring-file " + scan["tailoring"]) if scan.get("tailoring") else ""
    return "sudo oscap xccdf eval --profile %s%s --rule %s %s" % (scan.get("profile") or "stig", tail, ids[0], ds)


def answers_why(scanner, answers, read="", rules=None):
    """Whether this run gives Evaluate-STIG the answer files from your share (check_stig_evaluate_answers), and when not,
    why - one line for the job output, whatever the settings (rules: the recheck's rules to check again, None = the
    drift report)."""
    if str(scanner) != "evaluate_stig":
        return ("answer files: NOT USED - the scanner is %s (answer files are Evaluate-STIG's: check_stig_scanner: evaluate_stig)"
                % (scanner or "openscap"))
    if not answers:
        return ("answer files: NOT USED - check_stig_evaluate_answers is not set in this run, so Evaluate-STIG uses its own "
                "AnswerFiles folder on each host. To use your share: set it in this template's Variables (or "
                "playbooks/group_vars/all.yml, then sync the project)")
    if read:
        return ("answer files: NOT USED - check_stig_evaluate_read is set (%s): the job reads the checklist Evaluate-STIG "
                "already wrote on its own schedule, with its own answer files, and runs nothing" % read)
    if rules is not None and not len(rules):
        return "answer files: not needed - no rule to check again on any host"
    return "answer files: USED - read from %s, copied to each host for this run (the next tasks say what was read)" % answers


def answers_used(af, recheck=False):
    """What Evaluate-STIG did with the answer files it was given (site_stig_scan: answers.used, the rules whose
    checklist entry names an answer file) -> a line for the report and the job output (recheck: only some rules were
    checked again - none of them answered can be right)."""
    if not af.get("used") and recheck:
        return "none of the rules checked again was answered from them"
    if not af.get("used"):
        return ("NO rule answered from them - check that each file's STIG name matches the host's STIG and its answer "
                "key (DEFAULT, or the host's own) is in it")
    keys = af.get("keys") or []
    return "%d rule%s answered from them%s" % (af["used"], "" if af["used"] == 1 else "s",
                                               (" (answer key%s %s)" % ("" if len(keys) == 1 else "s", ", ".join(keys))) if keys else "")


def evaluate_note(scan):
    """How Evaluate-STIG ran, past the usual: with answer files copied down for it (check_stig_evaluate_answers); and what
    went wrong that the job worked around: its checklist read although its own schema check rejected it, its checks that
    failed ('' = nothing)."""
    out = []
    af = scan.get("answers") or {}
    if af:
        out.append("with %d answer file%s from %s" % (af.get("count", 0), "" if af.get("count") == 1 else "s", af.get("source") or "the share"))
        if "used" in af:
            out.append(answers_used(af, bool(scan.get("vulns_asked"))))
    if scan.get("evaluate_rejected"):
        out.append("its checklist failed Evaluate-STIG's own schema check (moved to %s) - read anyway"
                   % os.path.dirname(scan["evaluate_rejected"][0]))
    if scan.get("evaluate_failed"):
        out.append("its checks that failed: %s%s" % (", ".join(scan["evaluate_failed"]),
                                                                   (" - see " + scan["evaluate_log"]) if scan.get("evaluate_log") else ""))
    return "; ".join(out)


def stig_compare(scan, stigman=None, host="", severity=None, statuses=None, stigman_used=False, missing_severity="warning",
                 ignore=None, disagree_severity="none", scanner="openscap"):
    """One host: its scan (site_stig_scan's result), its STIG Manager record (site_stigman_read's hosts[host], {}
    when STIG Manager is not used) -> {host, scanned, ..., drift, new, disagree, fixed_open, unreviewed, ignored,
    by_hand, findings, row}."""
    scan = scan or {}
    sm = stigman or {}
    sev_map = dict(DEFAULT_SEVERITY, **(severity or {}))
    ok_status = statuses if statuses is not None else STIGMAN_ACCEPT
    win = scan.get("platform") == "windows"
    # a host with several checklists (Windows: one per STIG, web site or database instance): each is a result of its own,
    # its findings named for it
    each = scan.get("key") or ""
    tag = (":" + each) if each else ""
    say = (scan["label"] + ": ") if scan.get("label") else ""
    ign = ignore_set(ignore)
    out = {"host": host, "scanned": bool(scan.get("scanned")), "why": scan.get("why", ""), "os": scan.get("os", ""),
           "stig_version": scan.get("stig_version", ""), "ssg_version": scan.get("ssg_version", ""),
           "scanner": scan.get("scanner", "OpenSCAP"), "scanner_before": scan.get("scanner_before", ""),
           "evaluate_note": evaluate_note(scan), "label": ("%s - %s" % (host, scan["label"])) if scan.get("label") else "",
           "key": each, "benchmark": scan.get("benchmark", ""), "platform": scan.get("platform", ""),
           "checklist_time": scan.get("checklist_time", ""), "found_by": scan.get("found_by", ""),
           "profile": scan.get("profile_title") or scan.get("profile", ""), "counts": scan.get("counts") or {},
           "saved": scan.get("saved") or {}, "stigman": {k: sm.get(k, "") for k in ("status", "why", "collection", "asset", "revision")},
           "previous_time": (scan.get("previous") or {}).get("time", ""),
           "drift": [], "new": [], "disagree": [], "fixed_open": [], "unreviewed": [], "fixed": [], "ignored": [], "by_hand": None,
           "findings": []}
    if not out["scanned"] and win:
        out["findings"].append({"check": "stig", "id": "stig:not-scanned", "severity": "warning",
                                "summary": "the STIG scan did not run: " + (out["why"] or "unknown reason"),
                                "hint": (("Get-Content -Tail 40 '%s'" % scan["evaluate_log"]) if scan.get("evaluate_log") else
                                         ("Get-Item '%s'" % scan["evaluate"]) if scan.get("evaluate") else
                                         "Get-ChildItem -Path C:\\ -Filter Evaluate-STIG.ps1 -Recurse -Depth 5 -ErrorAction SilentlyContinue"),
                                "note": "Set win_check_stig_evaluate to Evaluate-STIG's folder on the server, or win_check_stig_evaluate_read to "
                                        "read the checklists it already writes, then run the job again for this server (docs/STIG_DRIFT.md)."})
        out["row"] = stig_host_row(out)
        return out
    if not out["scanned"]:
        es = scanner == "evaluate_stig"
        out["findings"].append({"check": "stig", "id": "stig:not-scanned", "severity": "warning",
                                "summary": "the STIG scan did not run: " + (out["why"] or "unknown reason"),
                                "hint": (("less " + scan["evaluate_log"]) if scan.get("evaluate_log") else ("ls -l " + (scan.get("evaluate") or "check_stig_evaluate")))
                                if es else "rpm -q openscap-scanner scap-security-guide",
                                "note": ("Check Evaluate-STIG on the host (check_stig_evaluate, check_stig_evaluate_read; pwsh installed), then run "
                                         "the job again for this host (docs/STIG_DRIFT.md).") if es else
                                        ("Install openscap-scanner and scap-security-guide (or check_stig_install: true), then run the job "
                                         "again for this host (docs/STIG_DRIFT.md).")})
        out["row"] = stig_host_row(out)
        return out
    af = scan.get("answers") or {}
    if af and "used" in af and not af["used"]:
        # given, not used: its rules came out as without them (Open, Not Reviewed) - the drift below is not to be trusted
        out["findings"].append({"check": "stig", "id": "stig:answers-unused" + tag, "severity": "warning",
                                "summary": "%sEvaluate-STIG was given %d answer file%s from %s and answered no rule from them"
                                           % (say, af.get("count", 0), "" if af.get("count") == 1 else "s", af.get("source") or "the share"),
                                "hint": "grep -o '<STIGComments Name=\"[^\"]*\"' *.xml   (in the answer files' folder: the STIG each file answers)",
                                "note": "Each answer file names the STIG it answers (STIGComments Name=, as Evaluate-STIG's own "
                                        "--ListSupportedProducts names it) and holds answer keys: DEFAULT, or a key named for the host "
                                        "(check_stig_evaluate_args: --AnswerKey ...). Fix the file on the share, then run the job again."})
    prev_items = (scan.get("previous") or {}).get("items") or {}
    before = scan.get("passed_before")
    if scan.get("history") == "items":     # Windows: the scan before and when each rule last passed are on the items
        prev_items = dict((it["key"], it["prev"]) for it in scan.get("items") or [] if it.get("prev"))
        before = dict((it["key"], it["passed"]) for it in scan.get("items") or [] if it.get("passed"))
    if not isinstance(before, dict):   # from an older site_stig_scan: the last scan's passes only
        before = dict((k, (scan.get("previous") or {}).get("time", "")) for k, v in prev_items.items() if v == "pass")
    found = sm.get("status") == "found"
    reviews = sm.get("reviews") or {} if found else {}
    checked = set()
    for it in scan.get("items") or []:
        key, now = it["key"], it.get("result")
        if now in CHECKED:
            checked.add(key)
        smr = reviews.get(key)
        sm_res = smr["result"] if smr and smr.get("status") in ok_status else None
        rec = dict(it, sm=sm_res, sm_status=(smr or {}).get("status", ""), sm_ts=(smr or {}).get("ts", ""),
                   prev=prev_items.get(key), passed=before.get(key, ""), title=_title(it, smr),
                   stigid=it.get("stigid") or (smr or {}).get("version", ""), level=sev_map.get(it.get("severity"), "warning"))
        rec["check"] = check_command(scan, rec)
        if _ignored(rec, ign):
            if now == "fail":
                out["ignored"].append(rec)
            continue
        if now == "fail" and sm_res in ("pass", "notapplicable"):
            out["drift" if key in before else "disagree"].append(rec)
        elif now == "fail" and key in before and sm_res != "fail":
            out["new"].append(rec)
        if now == "fail" and found and sm_res is None and key.startswith("SV-"):
            out["unreviewed"].append(rec)
        if now == "pass" and sm_res == "fail":
            out["fixed_open"].append(rec)
        elif now == "pass" and rec["prev"] == "fail":
            out["fixed"].append(rec)
    if found:
        out["by_hand"] = len([k for k, r in reviews.items() if r.get("status") in ok_status and k not in checked])
    by_sev = lambda r: (SEV_ORDER.get(r.get("severity"), 3), r["key"])
    for k in ("drift", "new", "disagree", "fixed_open", "unreviewed", "fixed", "ignored"):
        out[k].sort(key=by_sev)
    report = out["saved"].get("report") or ""
    where = (" Every rule, why it failed and its fix: the scan's report on the host, %s." % report) if report else ""
    if not report and out["saved"].get("results") and out["scanner"] == "Evaluate-STIG":
        report = out["saved"]["results"]
        where = " Evaluate-STIG's checklist is kept on the host, %s (STIG Manager imports it)." % report
    again = " To check the first one again on the host:"
    if out["drift"]:
        d = out["drift"]
        cmd = check_command(scan, d[0])
        out["findings"].append({"check": "stig", "id": "stig:drift" + tag, "severity": _worst(d, sev_map),
                                "summary": say + "%d STIG %s STIG Manager has as Not a Finding or Not Applicable %s now: %s" % (
                                    len(d), _plural(len(d), "rule", "rules"), _plural(len(d), "fails", "fail"), _short_list(d)),
                                "hint": cmd or report,
                                "note": "They passed on this host in an earlier scan: the host moved away from its checklist. Fix the host, or - "
                                        "if the change was approved - update the review in STIG Manager (this job never writes to it)."
                                        + where + (again if cmd else "")})
    if out["new"]:
        n = out["new"]
        cmd = check_command(scan, n[0])
        last = max((r.get("passed") or "")[:10] for r in n) or "-"
        out["findings"].append({"check": "stig", "id": "stig:new" + tag, "severity": _worst(n, sev_map),
                                "summary": say + "%d STIG %s passed in an earlier scan (last %s) and %s now: %s" % (
                                    len(n), _plural(len(n), "rule", "rules"), last, _plural(len(n), "fails", "fail"), _short_list(n)),
                                "hint": cmd or report,
                                "note": "Something changed on the host since they passed (the scans are kept on the host, check_stig_dir). "
                                        "STIG Manager has no Not a Finding for them, so they are not drift from the checklist - but they are new."
                                        + where + (again if cmd else "")})
    if out["disagree"] and disagree_severity in ("warning", "critical"):
        g = out["disagree"]
        cmd = check_command(scan, g[0])
        out["findings"].append({"check": "stig", "id": "stig:disagree" + tag, "severity": disagree_severity,
                                "summary": say + "%d STIG %s STIG Manager has as Not a Finding or Not Applicable %s failed in every scan of this host: %s" % (
                                    len(g), _plural(len(g), "rule", "rules"), _plural(len(g), "has", "have"), _short_list(g)),
                                "hint": cmd or report,
                                "note": "The host was like this before its first scan, or the scanner checks the rule another way than DISA's check "
                                        "text. Look at each once: fix the host, set the review to Open, or - the scanner is wrong for your site - add "
                                        "the rule to check_stig_ignore_rules." + where + (again if cmd else "")})
    # Windows: a STIG the asset does not have in STIG Manager is not compared, and no finding (every STIG Evaluate-STIG
    # finds is scanned; the site tracks the ones it assigned); the asset not there at all is one finding for the host
    if stigman_used and not found and missing_severity != "none" and not (win and sm.get("status") == "no_stig"):
        out["findings"].append({"check": "stig", "id": "stig:stigman", "severity": missing_severity,
                                "summary": "not compared with STIG Manager: " + (sm.get("why") or "not read"),
                                "hint": "",
                                "note": "Set the asset's name and collection for this host: check_stig_stigman_asset, check_stig_stigman_collection "
                                        "(docs/STIG_DRIFT.md)."
                                        + (" A web site or database checklist is the asset STIG Manager named <host>-<site>-<instance> "
                                           "when it imported it." if (scan.get("site") or scan.get("instance")) else "")})
    out["row"] = stig_host_row(out)
    return out


def stig_host_row(c):
    """The every-host table's row and its status."""
    cnt = c.get("counts") or {}
    sm = c.get("stigman") or {}
    if not c.get("scanned"):
        return {"cells": [_who(c), "not scanned: " + c.get("why", "")] + [""] * 10, "status": "warning"}
    if sm.get("status") == "found":
        smtxt = "%s / %s%s" % (sm.get("collection", ""), sm.get("asset", ""), (" (" + sm["revision"] + ")") if sm.get("revision") else "")
    elif sm.get("status"):
        smtxt = "not compared: " + sm.get("why", "")
    else:
        smtxt = "not used"
    status = "critical" if any(f["severity"] == "critical" for f in c["findings"]) else (
        "warning" if c["findings"] else "ok")
    tool = ("(SSG " + c["ssg_version"] + ")") if c.get("ssg_version") else ("(Evaluate-STIG)" if c.get("scanner") == "Evaluate-STIG" else "")
    if c.get("scanner_before"):
        tool += " - first scan with it, after %s: not compared with the earlier scans" % c["scanner_before"]
    if c.get("evaluate_note"):
        tool += " - " + c["evaluate_note"]
    if c.get("checklist_time"):
        tool += " - its checklist of %s" % c["checklist_time"][:10]
    return {"cells": [_who(c), "%s %s" % (c.get("stig_version", ""), tool),
                      smtxt, str(cnt.get("pass", 0)), str(cnt.get("fail", 0)), str(len(c["drift"])), str(len(c["new"])),
                      str(len(c.get("disagree") or [])), str(len(c["fixed_open"])), "-" if c.get("by_hand") is None else str(c["by_hand"]),
                      str(len(c.get("ignored") or [])), (c.get("saved") or {}).get("report", "") or (c.get("saved") or {}).get("results", "")
                      or "(not kept: dry run)"],
            "status": status}


def _rows(results, key, cols, chips):
    """The rows of one kind from every host, their status (the rule's CAT) and each cell's chip."""
    rows, rs, cs = [], [], []
    for c in results:
        for r in c.get(key) or []:
            rows.append(cols(c, r))
            rs.append(r.get("level") or {"high": "critical"}.get(r.get("severity"), "warning"))
            cs.append(chips(r))
    return rows, rs, cs


def _sm_cell(r):
    return "%s%s" % (SM_WORD.get(r["sm"], r["sm"]), (" (" + r["sm_status"] + ")") if r.get("sm_status") else "")


def _box(label, n, bad, note):
    return {"label": label, "value": n, "status": bad if n else "ok", "note": note}


def stig_cards(results, max_cards=6):
    """Start here: one card per CAT I rule that drifted or fails new (drifted first, then by host) - the rule, its host,
    when it last passed, STIG Manager's review, and the command that checks it again on the host. CAT II and III,
    and the rest, are in the tables -> (cards, how many CAT I rules were left out)."""
    found = []
    for kind, label in (("drift", "Drifted"), ("new", "New fail")):
        for c in sorted(results, key=_who):
            for r in c.get(kind) or []:
                if r.get("severity") == "high":
                    found.append((label, c, r))
    cards = []
    for label, c, r in found[:int(max_cards)]:
        if (c.get("stigman") or {}).get("status") != "found":      # no STIG Manager to point at
            sm, what = "", "Fix the host."
        else:
            sm = ("STIG Manager: %s. " % _sm_cell(r)) if r.get("sm") else "No review in STIG Manager. "
            what = ("Fix the host, or - if the change was approved - update the review in STIG Manager." if label == "Drifted"
                    else "Fix the host, or set the review to Open.")
        cards.append({"status": r.get("level") or "critical", "chip": "CAT I", "meta": label, "title": r["title"],
                      "subject": "%s - %s" % (_who(c), _rule_label(r)),
                      "next": "Passed %s, fails now. %s%s%s" % ((r.get("passed") or "before")[:10], sm, what,
                                                               " To check it again on the host:" if r.get("check") else
                                                               (" What Evaluate-STIG found:" if r.get("details") else "")),
                      "code": r.get("check") or details_text(r.get("details")) or ""})
    return cards, max(len(found) - int(max_cards), 0)


def stig_details_cards(results, list_max=200):
    """What Evaluate-STIG found, in full, for the drifted, new and disagree rules - one card each (the table cells keep
    the first line) -> (cards, how many there were)."""
    found = []
    for kind, label in (("drift", "Drifted"), ("new", "New fail"), ("disagree", "Disagree")):
        for c in sorted(results, key=_who):
            for r in c.get(kind) or []:
                t = details_text(r.get("details"))
                if t:
                    found.append({"status": r.get("level") or "warning", "chip": CAT.get(r.get("severity"), r.get("severity", "")), "meta": label,
                                  "title": r["title"], "subject": "%s - %s" % (_who(c), _rule_label(r)), "code": t})
    return found[:int(list_max)], len(found)


def stig_report_sections(results, list_max=200):
    """Every host's stig_compare result -> the report's sections: what moved (drift, new fails), where the scan and the
    checklist disagree, what the checklist is behind on, then every host, the rules left out, and the fails STIG
    Manager has no review for. Each table's CAT, scan and STIG Manager cells carry their status (cell_status): the
    report website (atlas) draws them as chips with icons, the emails in their colours."""
    results = [r for r in (results or []) if r]
    sections = []
    cols = ["Host", "STIG rule", "CAT", "Title"]
    drift_rows, drift_rs, drift_cs = _rows(results, "drift", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL", (r.get("passed") or "-")[:10], _sm_cell(r)],
        lambda r: ["", "", r["level"], "", "critical", "", SM_CHIP.get(r["sm"], "")])
    new_rows, new_rs, new_cs = _rows(results, "new", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], (r.get("passed") or "-")[:10], "FAIL",
        _sm_cell(r) if r.get("sm") else "no review"],
        lambda r: ["", "", r["level"], "", "", "critical", SM_CHIP.get(r.get("sm"), "unknown")])
    dis_rows, dis_rs, dis_cs = _rows(results, "disagree", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL", _sm_cell(r),
        ", ".join(r.get("scap") or []) or _details_short(r)],
        lambda r: ["", "", r["level"], "", "critical", SM_CHIP.get(r["sm"], ""), ""])
    fo_rows, fo_rs, fo_cs = _rows(results, "fixed_open", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "pass",
        "Open (%s%s)" % (r.get("sm_status") or "-", (", " + r["sm_ts"][:10]) if r.get("sm_ts") else "")],
        lambda r: ["", "", r["level"], "", "ok", "warning"])
    ig_rows, ig_rs, ig_cs = _rows(results, "ignored", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL",
        _sm_cell(r) if r.get("sm") else "no review"],
        lambda r: ["", "", r["level"], "", "critical", SM_CHIP.get(r.get("sm"), "unknown")])
    un_rows, un_rs, un_cs = _rows(results, "unreviewed", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL"],
        lambda r: ["", "", r["level"], "", "critical"])
    disagree_found = any(f["id"] == "stig:disagree" for c in results for f in c.get("findings") or [])
    not_scanned = [c for c in results if not c.get("scanned")]
    worst = lambda rs: "critical" if "critical" in rs else "warning"
    boxes = [_box("Drifted", len(drift_rows), worst(drift_rs), "passed before, now fails its Not a Finding"),
             _box("New fails", len(new_rows), worst(new_rs), "passed in an earlier scan"),
             _box("Disagree", len(dis_rows), "warning" if disagree_found else "info", "never passed here, Not a Finding: look once"),
             _box("Fixed, still Open", len(fo_rows), "info", "update STIG Manager"),
             _box("Not scanned", len(not_scanned), "warning", "no result for these hosts")]
    if drift_rows:
        sections.append({"title": "1. Drifted - the host fails what STIG Manager has as Not a Finding", "columns": cols + ["Scan now", "Last passed", "STIG Manager"],
                         "rows": drift_rows, "row_status": drift_rs, "cell_status": drift_cs, "status": worst(drift_rs),
                         "text": "These rules passed on the host in an earlier scan and fail now, and the checklist has them as Not a Finding: the host "
                                 "moved away from its checklist. Fix the host, or - if the change was approved - update the review in STIG Manager "
                                 "(this job never writes to it). They stay here every run until one of the two is done. CAT I is critical, CAT II "
                                 "and III a warning (check_stig_severity)."})
    if new_rows:
        sections.append({"title": "2. New fails - passed in an earlier scan", "columns": cols + ["Last passed", "Scan now", "STIG Manager"],
                         "rows": new_rows, "row_status": new_rs, "cell_status": new_cs, "status": worst(new_rs),
                         "text": "Something changed on the host since these passed (the scans are kept on the host, check_stig_dir). STIG Manager "
                                 "has no Not a Finding for them, so they are not drift from the checklist - but they are new. Once the review is "
                                 "Open, they leave this table."})
    if dis_rows:
        sections.append({"title": "3. The scan and the checklist disagree - look at each once", "columns": cols + ["Scan now", "STIG Manager", "The scan's check"],
                         "rows": dis_rows, "row_status": dis_rs if disagree_found else ["info"] * len(dis_rows), "cell_status": dis_cs,
                         "status": worst(dis_rs) if disagree_found else "info",
                         "text": "The checklist has these as Not a Finding, and they have failed in every scan of the host: nothing moved that "
                                 "this job saw. Either the host was like this before its first scan, or the scanner checks the rule another way "
                                 "than DISA's check text (OpenSCAP's check, or what Evaluate-STIG found, is the last column; the scan's report "
                                 "on the host shows more). For each: fix the host, set the review to Open, or - the scanner is wrong for your "
                                 "site - add the rule to check_stig_ignore_rules. "
                                 + ("They are findings here (check_stig_disagree_severity)." if disagree_found
                                    else "Report only (check_stig_disagree_severity: warning makes them findings).")})
    if fo_rows:
        sections.append({"title": "4. Fixed on the host, still Open in STIG Manager - the checklist is behind", "columns": cols + ["Scan now", "STIG Manager"],
                         "rows": fo_rows, "row_status": ["info"] * len(fo_rows), "cell_status": fo_cs, "status": "info",
                         "text": "No finding: the host passes. Update these reviews in STIG Manager (and close their POA&M items)."})
    if not (drift_rows or new_rows) and results:
        gone = (" %d host%s could not be scanned (below)." % (len(not_scanned), "" if len(not_scanned) == 1 else "s") if not_scanned else "")
        if dis_rows:
            # not "every host passes": the scan fails rules the checklist has as Not a Finding - they never passed here, so not drift
            hosts = len(set(c["host"] for c in results if c.get("disagree")))
            line = ("No drift: nothing that passed in an earlier scan fails now. But the scan fails %d STIG rule%s STIG Manager has as Not a "
                    "Finding or Not Applicable, on %d host%s (3. The scan and the checklist disagree): not drift, because %s never "
                    "passed in a scan here - the host's first scan, or its first with this scanner. "
                    % (len(dis_rows), "" if len(dis_rows) == 1 else "s", hosts, "" if hosts == 1 else "s",
                       "it has" if len(dis_rows) == 1 else "they have")
                    + ("They are findings (check_stig_disagree_severity)." if disagree_found
                       else "Report only: check_stig_disagree_severity: warning (or critical) makes them findings that fail the job."))
            sections.append({"title": "No drift - but the scan and the checklist disagree", "status": "warning" if disagree_found else "info",
                             "lines": [line + gone]})
        else:
            sections.append({"title": "No drift", "status": "ok",
                             "lines": ["Every host scanned passes what STIG Manager has as Not a Finding and passed before, and nothing that passed "
                                       "in an earlier scan fails now." + gone]})
    for s in sections:
        s["top"] = True
    if sections:
        sections[0]["boxes"] = boxes
        cards, more = stig_cards(results)
        sections[0]["start_here"] = cards
        sections[0]["start_here_text"] = ("The CAT I rules that moved: drifted from the checklist, or failing since they passed. CAT II and III "
                                          "are in the tables below%s." % (", with %d more CAT I" % more if more else ""))
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    hosts = sorted(results, key=lambda c: (order.get(c["row"]["status"], 3), _who(c)))
    hrs = [c["row"]["status"] for c in hosts]
    sections.append({"title": "Every host - the scan, STIG Manager, the counts", "status": "critical" if "critical" in hrs else ("warning" if "warning" in hrs else "ok"),
                     "columns": ["Host", "STIG (scan)", "STIG Manager (collection / asset)", "Pass", "Fail", "Drifted", "New", "Disagree",
                                 "Fixed, open", "By hand", "Left out", "The report on the host"],
                     "rows": [c["row"]["cells"] for c in hosts], "row_status": hrs,
                     "text": "Pass and Fail count STIG rules (several SCAP checks of one rule count once, one SCAP check of several rules "
                             "counts for each); the rest are not applicable or not checked automatically. By hand: the rules the checklist "
                             "has a review of that the scan gives no result for - DISA's manual checks (Evaluate-STIG: Not Reviewed), never "
                             "compared by this job. Left out: fails of the rules in check_stig_ignore_rules. Each scan's report (OpenSCAP), "
                             "results or checklist (Evaluate-STIG) and summary stay on the host."})
    full = stig_details_cards(results, list_max)
    if full[0]:
        sections.append({"title": "What Evaluate-STIG found - in full", "status": "info", "cards": full[0],
                         "text": "For each rule above that drifted, fails new or disagrees with the checklist: what Evaluate-STIG found on the "
                                 "host, as it wrote it in the checklist (its own header and separators left out)."
                                 + (" The first %d of %d (check_stig_list_max); the rest are in the checklist kept on the host."
                                    % (list_max, full[1]) if full[1] > list_max else "")})
    if ig_rows:
        sections.append({"title": "Left out on purpose - check_stig_ignore_rules", "columns": cols + ["Scan now", "STIG Manager"],
                         "rows": ig_rows[:list_max], "row_status": ["info"] * len(ig_rows[:list_max]), "cell_status": ig_cs[:list_max], "status": "info",
                         "text": "Never a finding: the scan fails these, and check_stig_ignore_rules says the scanner is known to get them "
                                 "wrong here. Take a rule off the list when its check is fixed (a newer scap-security-guide or Evaluate-STIG)."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(ig_rows)) if len(ig_rows) > list_max else "")})
    if un_rows:
        sections.append({"title": "Fails STIG Manager has no review for", "columns": cols + ["Scan now"], "rows": un_rows[:list_max],
                         "row_status": ["info"] * len(un_rows[:list_max]), "cell_status": un_cs[:list_max], "status": "info",
                         "text": "Report only: the checklist has no result for these rules yet (a rule new in the scan's STIG release, too)."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(un_rows)) if len(un_rows) > list_max else "")})
    return sections


def stig_lines(c):
    """One host's result in plain lines, for the job's output."""
    if not c.get("scanned"):
        return ["%s: NOT SCANNED - %s" % (_who(c), c.get("why", ""))]
    cnt = c.get("counts") or {}
    sm = c.get("stigman") or {}
    out = ["%s: %s %s - %d pass, %d fail; drifted %d, new %d, fixed but open %d; STIG Manager: %s; disagree %d, by hand %s, left out %d" % (
        _who(c), c.get("profile", ""), c.get("stig_version", ""), cnt.get("pass", 0), cnt.get("fail", 0), len(c["drift"]), len(c["new"]),
        len(c["fixed_open"]), ("%s / %s%s" % (sm.get("collection"), sm.get("asset"), (" (" + sm["revision"] + ")") if sm.get("revision") else ""))
        if sm.get("status") == "found" else (sm.get("why") or "not used"), len(c.get("disagree") or []),
        "-" if c.get("by_hand") is None else c["by_hand"], len(c.get("ignored") or []))]
    if c.get("evaluate_note"):
        out.append("  Evaluate-STIG: " + c["evaluate_note"])
    if c.get("checklist_time"):
        out.append("  its checklist of %s, read (Evaluate-STIG was not run)" % c["checklist_time"][:10])
    for r in c["drift"]:
        out.append("  DRIFT %s %s %s - STIG Manager: %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90], SM_WORD.get(r["sm"], r["sm"])))
    for r in c["new"]:
        out.append("  NEW   %s %s %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90]))
    for r in c.get("disagree") or []:
        # OpenSCAP: its check's name; Evaluate-STIG has none - the start of what it found
        found = re.sub(r"\s+", " ", details_text(r.get("details")))
        why = ("the scan's check: " + ", ".join(r["scap"])) if r.get("scap") else \
            ("Evaluate-STIG found: " + (found[:160].rsplit(" ", 1)[0] + " ..." if len(found) > 160 else found)) if found else "the scan: Open"
        out.append("  DISAGREE %s %s %s - STIG Manager: %s; %s" % (
            _rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90], SM_WORD.get(r["sm"], r["sm"]), why))
    if (c.get("saved") or {}).get("report"):
        out.append("  report on the host: " + c["saved"]["report"])
    elif not c.get("saved"):
        out.append("  nothing kept on the host (not kept: dry run)")
    return out


def stig_findings(results):
    """One host's findings from its results (Windows: one per checklist): each finding once; "not compared with STIG
    Manager" only when none of the host's checklists was (a web site's asset can be missing while the host's is there)."""
    results = [r for r in (results or []) if r]
    compared = any((r.get("stigman") or {}).get("status") in ("found", "no_stig") for r in results)
    out, seen = [], set()
    for r in results:
        for f in r.get("findings") or []:
            if f["id"] in seen or (compared and f["id"] == "stig:stigman"):
                continue
            seen.add(f["id"])
            out.append(f)
    return out


# ---- The STIG recheck (playbooks/stig_recheck.yml, win_stig_recheck.yml) ----------------------------------------------
# What STIG Manager has Open or Not Reviewed for a host, checked again on the host (only those rules) - nothing kept with
# the drift report's scans:
#   fixed        Open in STIG Manager, the host passes now: set the review to Not a Finding
#   now_na       Open in STIG Manager, Not Applicable now: set it to Not Applicable
#   still_open   Open in STIG Manager, the host still fails it: the review is right
#   answered     Not Reviewed in STIG Manager (no review, or not counted - rejected): the scan has an answer to set
#   by_hand      the scan gave no answer (a manual check, or not in its checklist): a person reviews it
RECHECK_WHICH = ("open", "not_reviewed")


def _sm_checklists(sm):
    """site_stigman_read's answer for a host -> its checklists (Linux: the host's one; Windows, every: true: each STIG of
    the asset and of its web site and database assets)."""
    sm = sm or {}
    if sm.get("status") != "found":
        return []
    return list(sm["checklists"]) if "checklists" in sm else [sm]


def recheck_asked(smc, statuses=None, which=None):
    """One checklist in STIG Manager -> the rules to check again: {SV id: (open | not_reviewed, its review or None)}. Open: a
    review that counts (check_stig_stigman_statuses) of Open; Not Reviewed: a rule of the STIG revision with no review that
    counts, or one of Not Reviewed."""
    ok = statuses if statuses is not None else STIGMAN_ACCEPT
    which = list(which) if which is not None else list(RECHECK_WHICH)
    reviews = smc.get("reviews") or {}
    rules = smc.get("rules")
    out = {}
    for k in sorted(rules.keys() if rules else reviews.keys()):
        r = reviews.get(k)
        counted = r if r and r.get("status") in ok else None
        if counted and counted.get("result") == "fail":
            if "open" in which:
                out[k] = ("open", counted)
        elif not counted or counted.get("result") in ("notchecked", ""):
            if "not_reviewed" in which:
                out[k] = ("not_reviewed", r)
    return out


def stig_recheck_vulns(sm, statuses=None, which=None):
    """A host's answer from STIG Manager -> the STIG rules to check again on it (V-257985 ...), every checklist's."""
    out = set()
    for smc in _sm_checklists(sm):
        rules = smc.get("rules") or {}
        for k in recheck_asked(smc, statuses, which):
            out.add((rules.get(k) or {}).get("v") or ("V-" + k[3:]))
    return sorted(out)


def _scan_for(smc, scans):
    """The scan's checklist of a checklist in STIG Manager: the same STIG, the same web site or database instance (Linux:
    the one scan); else the only one of that STIG."""
    scans = [s for s in scans or [] if s and s.get("scanned")]
    if "checklists" not in smc and len(scans) == 1 and not scans[0].get("key"):
        return scans[0]
    b = (smc.get("benchmark") or "").lower()
    same = [s for s in scans if (s.get("benchmark") or "").lower() == b]
    for s in same:
        if (s.get("site") or "") == (smc.get("site") or "") and (s.get("instance") or "") == (smc.get("instance") or ""):
            return s
    return same[0] if len(same) == 1 else None


def _rev_key(rev):
    """'V2R10' -> (2, 10); None when it is not one."""
    m = re.search(r"V(\d+)R(\d+)", (rev or "").upper())
    return (int(m.group(1)), int(m.group(2))) if m else None


def other_revision(scan_rev, sm_rev):
    """The scan's STIG revision and STIG Manager's differ (both known)."""
    a, b = _rev_key(scan_rev), _rev_key(sm_rev)
    return bool(a and b and a != b)


RULE_REV = re.compile(r"SV-\d+r\d+")


def rule_changed(scan_rule, sm_rule, other):
    """The scan checked another version of the rule than the one STIG Manager reviews: their rule ids differ
    (SV-230223r1017xxx_rule - DISA gives a rule a new one when it changes it; STIG Manager keeps a review of each
    apart). Without both ids: the revisions differ (other). Such an answer is to another check - never written."""
    a, b = RULE_REV.match(scan_rule or ""), RULE_REV.match(sm_rule or "")
    if a and b:
        return a.group(0) != b.group(0)
    return bool(other)


def _recheck_how(scan):
    if scan.get("checklist_time"):
        return "its checklist of %s, read (Evaluate-STIG was not run)" % scan["checklist_time"][:10]
    if scan.get("select_note"):
        return "in full - " + scan["select_note"]
    if scan.get("selected"):
        return "only these rules" + (" (-SelectVuln)" if scan.get("scanner") == "Evaluate-STIG" else " (--rule)")
    if scan.get("vulns_asked"):
        return "in full - this Evaluate-STIG has no -SelectVuln"
    return ""


def _recheck_result(host, smc, sm, scan, asked, ign, sev_map, label, carry=False, statuses=None):
    rules = smc.get("rules") or {}
    out = {"host": host, "label": ("%s - %s" % (host, label)) if label else "", "benchmark": smc.get("benchmark", ""),
           "scanned": bool(scan and scan.get("scanned")), "why": (scan or {}).get("why", ""),
           "scanner": (scan or {}).get("scanner", ""), "stig_version": (scan or {}).get("stig_version", ""),
           "how": _recheck_how(scan or {}), "evaluate_note": evaluate_note(scan or {}), "saved": (scan or {}).get("saved") or {},
           "stigman": {"status": sm.get("status", ""), "why": sm.get("why", ""), "collection": sm.get("collection", ""),
                       "asset": smc.get("asset", "") or sm.get("asset", ""), "revision": smc.get("revision", "")},
           # what a write needs (playbooks/stigman_update.yml): the collection's and the asset's ids, when the scan answered
           "collectionId": str(sm.get("collectionId") or ""), "assetId": str(smc.get("assetId") or sm.get("assetId") or ""),
           "scan_time": (scan or {}).get("checklist_time") or (scan or {}).get("time") or "",
           "asked_open": len([1 for w, _ in asked.values() if w == "open"]),
           "asked_not_reviewed": len([1 for w, _ in asked.values() if w == "not_reviewed"]),
           "fixed": [], "now_na": [], "still_open": [], "answered": [], "by_hand": [], "ignored": [], "held": [], "carried": [],
           "findings": []}
    items = dict((it["key"], it) for it in (scan or {}).get("items") or [])
    # the scan of another STIG revision than STIG Manager's (V2R8 / V2R9): an answer to a rule that changed between
    # them is to the old check - said, never written (rule_changed); the rules that did not change are as before
    sm_rev = smc.get("revision", "")
    other = other_revision(out["stig_version"], sm_rev)
    out["revision_other"] = ("scanned with %s, STIG Manager has %s" % (out["stig_version"], sm_rev)) if other and out["scanned"] else ""
    earlier = smc.get("earlier") or {}
    counts = statuses if statuses is not None else STIGMAN_ACCEPT
    # not rechecked: its rules are counted (asked), not sorted
    for k, (what, r) in sorted(asked.items()) if out["scanned"] else []:
        info = rules.get(k) or {}
        it = items.get(k) or {}
        sev = info.get("severity") or (r or {}).get("severity") or it.get("severity") or ""
        now = it.get("result") if it else None
        rec = {"key": k, "v": info.get("v") or it.get("v") or ("V-" + k[3:]), "stigid": info.get("version") or (r or {}).get("version") or it.get("stigid", ""),
               "severity": sev, "title": info.get("title") or (r or {}).get("title") or it.get("title") or k, "asked": what,
               "sm": (r or {}).get("result", ""), "sm_status": (r or {}).get("status", ""), "sm_ts": (r or {}).get("ts", ""),
               "sm_comment": bool((r or {}).get("comment")), "sm_revision": sm_rev,
               # a review of the rule in an earlier revision, whose check changed since (STIG Manager keeps it there)
               "sm_earlier": dict((x, (earlier.get(k) or {}).get(x, "")) for x in ("result", "status", "revision")) if earlier.get(k) else {},
               # the review's own rule (it may be of an earlier revision: STIG Manager keeps one review for both), else the asset's
               "rule_id": (r or {}).get("rule") or info.get("rule") or "",
               "now": now or "", "details": it.get("details", ""), "scap": it.get("scap") or [], "level": sev_map.get(sev, "warning")}
        if _ignored(rec, ign):
            out["ignored"].append(rec)
        elif (now in ("pass", "notapplicable") or (what == "not_reviewed" and now in CHECKED)) and \
                rule_changed(it.get("rule"), info.get("rule") or (r or {}).get("rule"), other):
            rec["changed"] = True
            rec["no_answer"] = "%s - the scan says %s, not written (update the scan's STIG to %s, or review it)" % (
                "the rule changed - the scan checked %s, STIG Manager has %s" % (it.get("rule"), info.get("rule")) if
                RULE_REV.match(it.get("rule") or "") and RULE_REV.match(info.get("rule") or "") else
                "scanned with %s, STIG Manager has %s" % (out["stig_version"], sm_rev),
                SCAN_WORD.get(now, now), sm_rev or "STIG Manager's revision")
            e = rec["sm_earlier"]
            # check_stig_recheck_carry_forward (0.69.0, opt-in): an earlier revision's counted Not a Finding / Not
            # Applicable that the old scan agrees with is written to the rule of the revision in use, said so
            if carry and what == "not_reviewed" and now in ("pass", "notapplicable") and e.get("result") == now and e.get("status") in counts:
                rec["carried_from"] = e.get("revision") or "an earlier revision"
                out["carried"].append(rec)
            else:
                out["held"].append(rec)
        elif what == "open" and now == "pass":
            out["fixed"].append(rec)
        elif what == "open" and now == "notapplicable":
            out["now_na"].append(rec)
        elif what == "open" and now == "fail":
            out["still_open"].append(rec)
        elif what == "not_reviewed" and now in CHECKED:
            out["answered"].append(rec)
        else:
            if it:
                rec["no_answer"] = "Not Reviewed by the scan"
            elif out["scanner"] == "OpenSCAP":
                rec["no_answer"] = "no SCAP check for it (a manual check)"
            else:
                rec["no_answer"] = "not in the scan's checklist (another STIG revision?)"
            out["by_hand"].append(rec)
    by_sev = lambda r: (SEV_ORDER.get(r.get("severity"), 3), r["key"])
    for k in ("fixed", "now_na", "still_open", "answered", "by_hand", "ignored", "held", "carried"):
        out[k].sort(key=by_sev)
    # held back (0.69.0: a list and a tile of their own, not Manual review - they need a newer scan, not a person): said in
    # the every-host row (a warning), the job's lines and the report - not a finding: the check's job fails only to ask
    # for the approval (workflow 37), and these ask nothing of it
    out["changed"] = len(out["held"])
    return out


def stig_recheck(sm, scans, host="", statuses=None, ignore=None, which=None, severity=None, stigman_used=True, carry_forward=False):
    """One host: its answer from STIG Manager (site_stigman_read: rules: true, or every: true on Windows) and its recheck
    scan (site_stig_scan with vulns - or win_facts.stig's scans) -> one result per checklist in STIG Manager:
    {host, label, scanned, how, stigman, asked_open, asked_not_reviewed, fixed, now_na, still_open, answered, by_hand,
    ignored, findings, row}. No finding for what was found: the report says what to update in STIG Manager; a host that
    could not be rechecked is a warning."""
    sm = sm or {}
    sev_map = dict(DEFAULT_SEVERITY, **(severity or {}))
    ign = ignore_set(ignore)
    scans = [s for s in (scans or []) if s]
    win = any(s.get("platform") == "windows" for s in scans)
    empty = {"fixed": [], "now_na": [], "still_open": [], "answered": [], "by_hand": [], "ignored": [], "held": [], "carried": [], "asked_open": 0,
             "asked_not_reviewed": 0, "how": "", "evaluate_note": "", "saved": {}, "scanner": "", "stig_version": "", "label": "", "benchmark": ""}
    if sm.get("status") != "found":
        why = sm.get("why") or ("STIG Manager is not set (check_stig_stigman_api)" if not stigman_used else "not read")
        out = dict(empty, host=host, scanned=False, why="nothing to recheck: " + why,
                   stigman={"status": sm.get("status", ""), "why": why, "collection": "", "asset": "", "revision": ""},
                   findings=[{"check": "stig", "id": "stig:stigman", "severity": "warning",
                              "summary": "not rechecked - what to recheck is read from STIG Manager: " + why, "hint": "",
                              "note": "Set the asset's name and collection for this host: check_stig_stigman_asset, check_stig_stigman_collection "
                                      "(docs/STIG_DRIFT.md, The STIG recheck)."}])
        out["row"] = stig_recheck_row(out)
        return [out]
    results = []
    lists = _sm_checklists(sm)
    asked_any = False
    for smc in lists:
        asked = recheck_asked(smc, statuses, which)
        if not asked:
            continue
        asked_any = True
        scan = _scan_for(smc, scans)
        label = ""
        if win:
            label = (scan or {}).get("label") or smc.get("benchmark", "")
            if not scan and (smc.get("site") or smc.get("instance")):
                label += " - " + " / ".join(x for x in (smc.get("site"), smc.get("instance")) if x)
        r = _recheck_result(host, smc, sm, scan, asked, ign, sev_map, label, bool(carry_forward), statuses)
        if not scan:
            failed = next((s for s in scans if not s.get("scanned")), None)
            r["why"] = (failed or {}).get("why") or (
                "Evaluate-STIG wrote no checklist of %s%s (it may not apply to this server, or win_check_stig_evaluate_select / "
                "_exclude left it out)" % (smc.get("benchmark", ""), (" for " + smc["site"]) if smc.get("site") else "")
                if scans else "the scan gave no result")
        results.append(r)
    if not asked_any:
        out = dict(empty, host=host, scanned=True, why="",
                   stigman={"status": "found", "why": "", "collection": sm.get("collection", ""), "asset": sm.get("asset", ""),
                            "revision": sm.get("revision", "")}, findings=[], nothing=True)
        out["row"] = stig_recheck_row(out)
        return [out]
    failed = [s for s in scans if not s.get("scanned")]
    if failed or not scans:
        s = failed[0] if failed else {}
        why = s.get("why") or "the scan gave no result"
        if win:
            hint = (("Get-Content -Tail 40 '%s'" % s["evaluate_log"]) if s.get("evaluate_log") else ("Get-Item '%s'" % s["evaluate"]) if s.get("evaluate")
                    else "Get-ChildItem -Path C:\\ -Filter Evaluate-STIG.ps1 -Recurse -Depth 5 -ErrorAction SilentlyContinue")
        else:
            hint = ("less " + s["evaluate_log"]) if s.get("evaluate_log") else ""
        results[0]["findings"].append({"check": "stig", "id": "stig:not-scanned", "severity": "warning",
                                       "summary": "the STIG recheck did not run: " + why, "hint": hint,
                                       "note": "The same settings as the STIG drift report (docs/STIG_DRIFT.md): Evaluate-STIG or OpenSCAP on the host."})
    for r in results:
        r["row"] = stig_recheck_row(r)
    return results


def stig_recheck_row(c):
    """The every-host table's row of the recheck and its status."""
    sm = c.get("stigman") or {}
    smtxt = ("%s / %s%s" % (sm.get("collection", ""), sm.get("asset", ""), (" (" + sm["revision"] + ")") if sm.get("revision") else "")
             if sm.get("status") == "found" else "not read: " + sm.get("why", ""))
    if c.get("nothing"):
        return {"cells": [_who(c), "nothing to recheck: no rule Open or Not Reviewed", smtxt] + ["0"] * 7 + ["", ""], "status": "ok"}
    if not c.get("scanned"):
        return {"cells": [_who(c), "not rechecked: " + c.get("why", ""), smtxt, "%d / %d" % (c.get("asked_open", 0), c.get("asked_not_reviewed", 0))]
                + [""] * 8, "status": "warning"}
    tool = "%s %s" % (c.get("stig_version", ""), ("(" + c["scanner"] + ")") if c.get("scanner") else "")
    if c.get("evaluate_note"):
        tool += " - " + c["evaluate_note"]
    if c.get("changed"):
        tool += " - %d answer%s held back: the scan's STIG is older" % (c["changed"], "" if c["changed"] == 1 else "s")
    if c.get("carried"):
        tool += " - %d carried forward" % len(c["carried"])
    status = "warning" if c.get("changed") else "info" if (c["fixed"] or c["now_na"] or c["answered"] or c.get("carried")) else "ok"
    return {"cells": [_who(c), tool.strip(), smtxt, "%d / %d" % (c["asked_open"], c["asked_not_reviewed"]), str(len(c["fixed"]) + len(c["now_na"])),
                      str(len(c["answered"]) + len(c.get("carried") or [])), str(len(c["still_open"])), str(len(c["by_hand"])),
                      str(len(c.get("held") or [])), str(len(c["ignored"])), c.get("how", ""),
                      (c.get("saved") or {}).get("results", "") or "(not kept: dry run)"],
            "status": status}


def _no_review(rev, earlier):
    """'no review', and the review of an earlier revision when STIG Manager has one: 'no review in V2R9 - V2R8: Not a
    Finding (accepted)' (the rule's check changed: STIG Manager did not carry it over)."""
    e = earlier or {}
    if not e.get("result"):
        return "no review"
    return "no review%s - %s: %s (%s)" % ((" in " + rev) if rev else "", e.get("revision") or "an earlier revision",
                                          SM_WORD.get(e["result"], e["result"]), e.get("status") or "-")


def _ask_cell(r):
    """STIG Manager's review as it is: Open (accepted, 2026-08-15), Not a Finding (rejected, ...) - one that does not
    count - or no review (and the review of an earlier revision, when there is one)."""
    if not r.get("sm") and not r.get("sm_status"):
        return _no_review(r.get("sm_revision"), r.get("sm_earlier"))
    return "%s (%s%s)" % (SM_WORD.get(r.get("sm"), r.get("sm") or "Not Reviewed"), r.get("sm_status") or "-",
                          (", " + r["sm_ts"][:10]) if r.get("sm_ts") else "")


def stig_recheck_sections(results, list_max=200):
    """Every host's stig_recheck results -> the report's sections: what to update in STIG Manager (fixed, Not Applicable
    now, the Not Reviewed the scan answered), what is still open, what needs a person, and every host."""
    results = [r for r in (results or []) if r]
    cols = ["Host", "STIG rule", "CAT", "Title"]
    sections = []
    upd_rows, upd_rs, upd_cs = _rows(results, "fixed", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "pass", _ask_cell(r), "Not a Finding"],
        lambda r: ["", "", r["level"], "", "ok", "warning", "ok"])
    na_rows, na_rs, na_cs = _rows(results, "now_na", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "n/a", _ask_cell(r), "Not Applicable"],
        lambda r: ["", "", r["level"], "", "info", "warning", "info"])
    upd_rows, upd_rs, upd_cs = upd_rows + na_rows, upd_rs + na_rs, upd_cs + na_cs
    an_rows, an_rs, an_cs = _rows(results, "answered", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], SCAN_WORD.get(r["now"], r["now"]),
        _ask_cell(r), SM_WORD.get(r["now"], r["now"]), _details_short(r) if r["now"] == "fail" else ""],
        lambda r: ["", "", r["level"], "", SCAN_CHIP.get(r["now"], ""), "unknown", SM_CHIP.get(r["now"], ""), ""])
    so_rows, so_rs, so_cs = _rows(results, "still_open", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL", _ask_cell(r),
        ", ".join(r.get("scap") or []) or _details_short(r)],
        lambda r: ["", "", r["level"], "", "critical", "warning", ""])
    bh_rows, bh_rs, bh_cs = _rows(results, "by_hand", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], _ask_cell(r), r.get("no_answer", "")],
        lambda r: ["", "", r["level"], "", "warning" if r["asked"] == "open" else "unknown", ""])
    # 0.69.0: held back (the scan's STIG older - not Manual review) and carried forward (check_stig_recheck_carry_forward)
    he_rows, he_rs, he_cs = _rows(results, "held", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], _ask_cell(r), r.get("no_answer", "")],
        lambda r: ["", "", r["level"], "", "unknown", ""])
    ca_rows, ca_rs, ca_cs = _rows(results, "carried", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], SCAN_WORD.get(r["now"], r["now"]),
        _ask_cell(r), SM_WORD.get(r["now"], r["now"])],
        lambda r: ["", "", r["level"], "", SCAN_CHIP.get(r["now"], ""), "unknown", SM_CHIP.get(r["now"], "")])
    revs = next((c["revision_other"] for c in results if c.get("held") and c.get("revision_other")), "")
    ig_rows, ig_rs, ig_cs = _rows(results, "ignored", lambda c, r: [
        _who(c), _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], SCAN_WORD.get(r["now"], r["now"] or "-"), _ask_cell(r)],
        lambda r: ["", "", r["level"], "", SCAN_CHIP.get(r["now"], ""), ""])
    not_done = [c for c in results if not c.get("scanned")]
    head = recheck_headline(results)
    # 0.67.0, in the user's words: the middle tile is STIG Manager's reviews only - it read as changes to the hosts
    answered_fail = len([1 for c in results for r in c.get("answered") or [] if r.get("now") == "fail"])
    boxes = [_box("Open findings", head["open"], head["open_status"], "fail on the host now: fix, or put on the POA&M"),
             _box("Status updates", len(upd_rows) + len(an_rows) + len(ca_rows), "info",
                  "no fix needed: the host passes, no longer applies, or was answered - only STIG Manager's review changes" + (
                      " (%d answered as a fail: also under Open findings)" % answered_fail if answered_fail else "")),
             _box("Manual review", len(bh_rows), "info", "no automatic check: DISA's manual checks, or a rule not in the scan's STIG"),
             _box("Not rechecked", len(not_done), "warning", "no result for these")]
    if he_rows:
        boxes.insert(3, _box("Held back: scan's STIG older", len(he_rows), "warning",
                             "not written - the rule changed since the scan's STIG%s: update Evaluate-STIG to STIG Manager's revision" % (
                                 (" (" + revs + ")") if revs else "")))
    if upd_rows:
        sections.append({"title": "1. Open in STIG Manager - the host passes now: update the review", "columns": cols + ["Scan now", "STIG Manager", "Set it to"],
                         "rows": upd_rows, "row_status": ["info"] * len(upd_rows), "cell_status": upd_cs, "status": "info",
                         "text": "Checked again on the host just now: these pass (or no longer apply). Set the reviews in STIG Manager as the last "
                                 "column says (this job never writes to it), and close their POA&M items. The checklist kept on the host "
                                 "(the last column of Every host) has these results; STIG Manager can import it."})
    if an_rows:
        sections.append({"title": "2. Not Reviewed in STIG Manager - the scan has an answer", "columns": cols + ["Scan now", "STIG Manager", "Set it to", "What was found"],
                         "rows": an_rows[:list_max], "row_status": ["info"] * len(an_rows[:list_max]), "cell_status": an_cs[:list_max], "status": "info",
                         "text": "STIG Manager has no review that counts for these (none, Not Reviewed, or one rejected): the scan answered "
                                 "them. A fail is Open - and a finding to fix or put on the POA&M."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(an_rows)) if len(an_rows) > list_max else "")})
    if so_rows:
        sections.append({"title": "3. Still open - the scan agrees with STIG Manager", "columns": cols + ["Scan now", "STIG Manager", "What was found"],
                         "rows": so_rows[:list_max], "row_status": ["info"] * len(so_rows[:list_max]), "cell_status": so_cs[:list_max], "status": "info",
                         "text": "Open in STIG Manager and the host still fails them: nothing to update."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(so_rows)) if len(so_rows) > list_max else "")})
    if ca_rows:
        sections.append({"title": "4. Carried forward from an earlier revision (check_stig_recheck_carry_forward)",
                         "columns": cols + ["Scan now", "STIG Manager", "Set it to"],
                         "rows": ca_rows[:list_max], "row_status": ["info"] * len(ca_rows[:list_max]), "cell_status": ca_cs[:list_max], "status": "info",
                         "text": "DISA changed these rules since the scan's STIG, so STIG Manager has no review of them in its revision; the "
                                 "earlier revision's review counts and the scan (of that revision) agrees with it. Approved, the answer is "
                                 "written to the rule of STIG Manager's revision, with a comment that says it was carried forward - checked "
                                 "against the earlier revision's check, not this one. Update Evaluate-STIG to check them for real."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(ca_rows)) if len(ca_rows) > list_max else "")})
    if not (upd_rows or an_rows or ca_rows) and results:
        gone = (" %d could not be rechecked (below)." % len(not_done)) if not_done else ""
        sections.append({"title": "Nothing to update in STIG Manager", "status": "ok",
                         "lines": ["No rule STIG Manager has Open passes now, and the scan answered no rule it has Not Reviewed." + gone]})
    for s in sections:
        s["top"] = True
    if sections:
        # the title and status say what is open and what to update - the recheck raises no finding of its own, so
        # "no findings" and a green page would say nothing is open
        sections[0].update(boxes=boxes, headline=head["text"], headline_status=head["status"], summary_only=True)
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    hosts = sorted(results, key=lambda c: (order.get(c["row"]["status"], 3), _who(c)))
    hrs = [c["row"]["status"] for c in hosts]
    sections.append({"title": "Every host - what STIG Manager has Open / Not Reviewed, and the recheck", "status": "warning" if "warning" in hrs else (
        "info" if "info" in hrs else "ok"),
        "columns": ["Host", "STIG (scan)", "STIG Manager (collection / asset)", "Open / Not Reviewed", "Fixed", "Answered", "Still open",
                    "Manual review", "Held back", "Left out", "Checked", "The checklist on the host"],
        "rows": [c["row"]["cells"] for c in hosts], "row_status": hrs,
        "text": "Only the rules STIG Manager has Open or Not Reviewed (check_stig_recheck) were checked; nothing is kept with the STIG drift "
                "report's scans. Checked: only these rules (Evaluate-STIG's -SelectVuln, or oscap's --rule) - fast - or in full when "
                "this Evaluate-STIG has no -SelectVuln. The STIG drift report checks every rule."})
    full = []
    for kind, lab in (("still_open", "Still open"), ("answered", "Answered: Open")):
        for c in sorted(results, key=_who):
            for r in c.get(kind) or []:
                t = details_text(r.get("details"))
                if t and r.get("now") == "fail":
                    full.append({"status": r.get("level") or "warning", "chip": CAT.get(r.get("severity"), r.get("severity", "")), "meta": lab,
                                 "title": r["title"], "subject": "%s - %s" % (_who(c), _rule_label(r)), "code": t})
    if full:
        sections.append({"title": "What Evaluate-STIG found - in full", "status": "info", "cards": full[:list_max],
                         "text": "For each rule above that fails: what Evaluate-STIG found on the host (its own header and separators left out)."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(full)) if len(full) > list_max else "")})
    if he_rows:
        sections.append({"title": "Held back - the scan's STIG is older than STIG Manager's", "columns": cols + ["STIG Manager", "Why"],
                         "rows": he_rows[:list_max], "row_status": ["info"] * len(he_rows[:list_max]), "cell_status": he_cs[:list_max], "status": "warning",
                         "text": "The scan answered these, but DISA changed the rule since the scan's STIG (a new rule id): STIG Manager keeps "
                                 "the review of the old rule apart and has none in its revision, and the scan's answer is to the old check - "
                                 "so it is not written. They need a newer scan, not a person: update Evaluate-STIG to the revision STIG "
                                 "Manager uses and run the recheck again. Or set check_stig_recheck_carry_forward: true to write the earlier "
                                 "revision's answer where the scan agrees with it (said in each review's comment)."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(he_rows)) if len(he_rows) > list_max else "")})
    if bh_rows:
        sections.append({"title": "Manual review - the scan gave no answer", "columns": cols + ["STIG Manager", "Why"],
                         "rows": bh_rows[:list_max], "row_status": ["info"] * len(bh_rows[:list_max]), "cell_status": bh_cs[:list_max], "status": "info",
                         "text": "DISA's manual checks (Evaluate-STIG leaves them Not Reviewed unless an answer file covers them), and rules not in the "
                                 "scan's STIG revision: a person reviews these."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(bh_rows)) if len(bh_rows) > list_max else "")})
    if ig_rows:
        sections.append({"title": "Left out on purpose - check_stig_ignore_rules", "columns": cols + ["Scan now", "STIG Manager"],
                         "rows": ig_rows[:list_max], "row_status": ["info"] * len(ig_rows[:list_max]), "cell_status": ig_cs[:list_max], "status": "info",
                         "text": "The scanner is known to get these wrong here: not sorted above."})
    return sections


def recheck_headline(results):
    """The recheck in one line, and its status: what is open now (still open, and Not Reviewed rules the scan fails),
    what to update in STIG Manager, what a person reviews, what could not be rechecked."""
    results = [r for r in (results or []) if r]
    opened = [r for c in results for r in (c.get("still_open") or []) + [x for x in c.get("answered") or [] if x.get("now") == "fail"]]
    update = sum(len(c.get(k) or []) for c in results for k in ("fixed", "now_na", "answered", "carried"))
    hand = sum(len(c.get("by_hand") or []) for c in results)
    held = sum(len(c.get("held") or []) for c in results)
    gone = len([c for c in results if not c.get("scanned")])
    cat1 = len([r for r in opened if r.get("severity") == "high"])
    if not opened and not update and not hand and not gone and not held:
        text = "nothing open - STIG Manager has no rule Open or Not Reviewed that the scan fails"
    else:
        text = "%d rule%s open%s" % (len(opened), "" if len(opened) == 1 else "s", (" (CAT I: %d)" % cat1) if cat1 else "")
        text += ", %d status update%s in STIG Manager" % (update, "" if update == 1 else "s")
        if hand:
            text += ", %d for manual review" % hand
        if held:
            text += ", %d held back (the scan's STIG is older)" % held
        if gone:
            text += ", %d not rechecked" % gone
    open_status = "critical" if cat1 else ("warning" if opened else "ok")
    status = "critical" if cat1 else ("warning" if opened or gone else ("info" if update or hand or held else "ok"))
    return {"text": text, "status": status, "open": len(opened), "open_status": open_status}


def stig_recheck_lines(c):
    """One recheck result in plain lines, for the job's output."""
    sm = c.get("stigman") or {}
    smtxt = ("%s / %s%s" % (sm.get("collection"), sm.get("asset"), (" (" + sm["revision"] + ")") if sm.get("revision") else "")
             if sm.get("status") == "found" else (sm.get("why") or "not read"))
    if c.get("nothing"):
        return ["%s: nothing to recheck - STIG Manager has no rule Open or Not Reviewed; STIG Manager: %s" % (_who(c), smtxt)]
    if not c.get("scanned"):
        return ["%s: NOT RECHECKED - %s" % (_who(c), c.get("why", ""))]
    more = "".join(", %s %d" % (w, len(c.get(k) or [])) for k, w in (("held", "held back"), ("carried", "carried forward")) if c.get(k))
    out = ["%s: %d Open, %d Not Reviewed in STIG Manager - fixed %d, now n/a %d, answered %d, still open %d, by hand %d, left out %d%s; "
           "STIG Manager: %s; checked: %s" % (
               _who(c), c["asked_open"], c["asked_not_reviewed"], len(c["fixed"]), len(c["now_na"]), len(c["answered"]), len(c["still_open"]),
               len(c["by_hand"]), len(c["ignored"]), more, smtxt, c.get("how") or "-")]
    if c.get("evaluate_note"):
        out.append("  Evaluate-STIG: " + c["evaluate_note"])
    if c.get("changed"):
        out.append("  REVISION %s%d answer%s held back: the rule changed since the scan's STIG - update it" % (
            (c["revision_other"] + " - ") if c.get("revision_other") else "", c["changed"], "" if c["changed"] == 1 else "s"))
    for r in c["fixed"]:
        out.append("  FIXED  %s %s %s - set Not a Finding" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90]))
    for r in c["now_na"]:
        out.append("  N/A    %s %s %s - set Not Applicable" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90]))
    for r in c["answered"]:
        out.append("  ANSWER %s %s %s - set %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90], SM_WORD.get(r["now"], r["now"])))
    for r in c.get("carried") or []:
        out.append("  CARRY  %s %s %s - set %s, carried forward from %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90],
                                                                          SM_WORD.get(r["now"], r["now"]), r.get("carried_from", "")))
    if (c.get("saved") or {}).get("results"):
        out.append("  checklist on the host: " + c["saved"]["results"])
    elif not c.get("saved"):
        out.append("  nothing kept on the host (not kept: dry run)")
    return out


# ---- the STIG recheck, with an approval: what to write to STIG Manager (playbooks/stigman_update.yml) ------------------
UPDATE_KINDS = ("fixed", "now_na", "answered", "carried")   # carried: only with check_stig_recheck_carry_forward
UPDATE_STATUS = ("saved", "submitted", "accepted")
ENGINE = {"Evaluate-STIG": "script", "OpenSCAP": "scap"}


def stig_update_plan(results, kinds=None, status="submitted", detail_max=8000, day=""):
    """Every host's stig_recheck results -> the reviews to write to STIG Manager once approved, one per rule: [{host, label,
    collection, collectionId, asset, assetId, benchmark, ruleId, v, title, severity, kind, was: {result, status, ts}, result,
    status, detail, comment (None: the review's own kept), engine, line}]. Only what the recheck found and was asked for
    (kinds: fixed -> Not a Finding, now_na -> Not Applicable, answered -> what the scan said); still open, by hand and left
    out are never written. A rule without the ids a write needs is left out (none such from site_stigman_read)."""
    kinds = list(kinds) if kinds is not None else list(UPDATE_KINDS)
    out = []
    for c in results or []:
        if not c or not c.get("scanned") or not c.get("collectionId") or not c.get("assetId"):
            continue
        sm = c.get("stigman") or {}
        scanner = c.get("scanner") or "the scan"
        for kind in UPDATE_KINDS:
            if kind not in kinds and kind != "carried":         # carried is switched on by check_stig_recheck_carry_forward itself
                continue
            for r in c.get(kind) or []:
                to = "pass" if kind == "fixed" else "notapplicable" if kind == "now_na" else r.get("now")
                if to not in CHECKED or not r.get("rule_id"):
                    continue
                word = SM_WORD.get(to, to)
                detail = (r.get("details") or "").strip() or "%s on %s%s: %s" % (
                    scanner, c.get("host", ""), (" (" + ", ".join(r.get("scap") or []) + ")") if r.get("scap") else "", SCAN_WORD.get(to, to))
                if len(detail) > detail_max:
                    detail = detail[:detail_max - 40].rstrip() + "\n[... cut at %d characters]" % detail_max
                note = "Set by the STIG recheck (site-automation), approved in AAP: %s checked it again on %s%s - %s." % (
                    scanner, c.get("host", ""), (" on " + day) if day else "", word)
                # a finding needs its comment (the collection may require one); a review that has one keeps it otherwise
                comment = None if (r.get("sm_comment") and to != "fail") else note
                if kind == "carried":
                    e = r.get("sm_earlier") or {}
                    comment = ("Carried forward by the STIG recheck (site-automation), approved in AAP: %s (%s) in %s, and %s on %s%s "
                               "checked it against %s - not against %s (check_stig_recheck_carry_forward)." % (
                                   SM_WORD.get(e.get("result"), e.get("result")), e.get("status") or "-", r.get("carried_from", ""), scanner,
                                   c.get("host", ""), (" on " + day) if day else "", c.get("stig_version") or "its own STIG revision",
                                   r.get("sm_revision") or "this revision"))
                engine = {"type": ENGINE.get(c.get("scanner"), "other"), "product": c.get("scanner") or "site-automation"}
                if c.get("scan_time"):
                    engine["time"] = c["scan_time"]
                was = {"result": r.get("sm") or "", "status": r.get("sm_status") or "", "ts": r.get("sm_ts") or ""}
                asset = sm.get("asset") or c.get("host", "")
                out.append({"host": c.get("host", ""), "label": _who(c), "collection": sm.get("collection", ""), "collectionId": c["collectionId"],
                            "asset": asset, "assetId": c["assetId"], "benchmark": c.get("benchmark", ""), "ruleId": r["rule_id"],
                            "v": r.get("v", ""), "title": r.get("title", ""), "severity": r.get("severity", ""), "kind": kind,
                            "was": was, "earlier": r.get("sm_earlier") or {}, "revision": r.get("sm_revision") or "", "result": to, "status": status, "detail": detail, "comment": comment, "engine": engine,
                            "line": "%s %s %s: %s -> %s (%s%s)" % (asset, c.get("benchmark", ""), r.get("v", ""),
                                                                    SM_WORD.get(was["result"], "no review") if was["result"] else "no review", word, status,
                                                                    (", carried forward from " + r.get("carried_from", "")) if kind == "carried" else "")})
    return out


def stig_update_sections(plan, list_max=200):
    """The check's section in workflow 37: what Approve writes to STIG Manager, review by review."""
    plan = plan or []
    if not plan:
        return [{"title": "Nothing to write to STIG Manager", "status": "ok", "top": True,
                 "lines": ["No review to change: the approval step is not asked for."]}]
    rows = [[p["label"], "%s / %s" % (p["collection"], p["asset"]), p["benchmark"], p["v"], CAT.get(p["severity"], p["severity"]),
             p["title"], ("%s (%s)" % (SM_WORD.get(p["was"]["result"], "Not Reviewed"), p["was"]["status"] or "-")) if p["was"]["result"]
             else _no_review(p.get("revision"), p.get("earlier")),
             SM_WORD.get(p["result"], p["result"]), p["status"], "kept" if p["comment"] is None else "set"] for p in plan]
    return [{"title": "Approve writes these reviews to STIG Manager", "status": "warning", "top": True,
             "columns": ["Host", "Collection / asset", "STIG", "Rule", "CAT", "Title", "STIG Manager now", "Written as", "Status", "Comment"],
             "rows": rows[:list_max], "row_status": ["info"] * len(rows[:list_max]),
             "text": "The approval step's next job writes exactly these reviews - nothing else: no collection, asset, user or other "
                     "review is changed. Each is written only if it is still as it was read here (STIG Manager now); one changed since "
                     "is left, and said. The detail is what the scan found; a review that has a comment keeps it (a finding gets one)."
                     + (" The first %d of %d (check_stig_list_max)." % (list_max, len(rows)) if len(rows) > list_max else "")}]


UPDATE_BAD = ("rejected", "error", "bad", "not as written")


def stig_update_attachments(plan, reviews=None, error=""):
    """Workflow 37's check: the plan without the reviews that have attachments in STIG Manager (site_stigman_attachments:
    reviews, in the plan's order) -> {plan, left: [plan items, with attachments and names], section (for the report) or
    None}. Attachments that could not be read count as attachments; STIG Manager not read at all (error): nothing goes on."""
    plan = list(plan or [])
    if error:
        return {"plan": [], "left": plan, "section": {
            "title": "Nothing handed to the approval: attachments not checked", "status": "warning", "top": True,
            "lines": ["STIG Manager could not be read to see which reviews have attachments: %s" % error,
                      "Nothing goes to the approval step. check_stig_update_skip_attachments: false skips this check."]}}
    keep, left = [], []
    for i, p in enumerate(plan):
        r = (reviews or [])[i] if i < len(reviews or []) else {"attachments": -1, "error": "not read"}
        if r.get("attachments"):
            left.append(dict(p, attachments=r.get("attachments"), attachment_names=r.get("names") or [], attachment_error=r.get("error") or ""))
        else:
            keep.append(p)
    section = None
    if left:
        section = {"title": "Left as they are: reviews with attachments (%d)" % len(left), "status": "info", "top": True,
                   "columns": ["Host", "Collection / asset", "Rule", "STIG Manager now", "The scan says", "Attachments"],
                   "rows": [[p.get("label", ""), "%s / %s" % (p.get("collection", ""), p.get("asset", "")), p.get("v", ""),
                             ("%s (%s)" % (SM_WORD.get(p["was"]["result"], p["was"]["result"]), p["was"].get("status") or "-"))
                             if (p.get("was") or {}).get("result") else "no review",
                             SM_WORD.get(p.get("result"), p.get("result")),
                             (", ".join(p["attachment_names"][:5]) or "%d file(s)" % p["attachments"]) if p["attachments"] > 0
                             else "could not be read: %s" % (p["attachment_error"] or "-")] for p in left],
                   "text": "A person attached evidence to these reviews in STIG Manager: they are not handed to the approval and "
                           "never written by workflow 37 (check_stig_update_skip_attachments). Review them in STIG Manager if the "
                           "scan's result is right."}
    return {"plan": keep, "left": left, "section": section}


def stig_update_report(items, check=False, error="", planned=0):
    """playbooks/stigman_update.yml's report: each review of the plan, what became of it in STIG Manager."""
    items = items or []
    n = dict((k, len([i for i in items if i.get("state") == k])) for k in (
        "written", "would write", "already so", "changed since", "has attachments", "rejected", "error", "bad", "not as written"))
    bad = sum(n[k] for k in UPDATE_BAD)
    status = "critical" if (error or bad) else "warning" if n["changed since"] else "ok"
    chip = {"written": "ok", "would write": "info", "already so": "ok", "changed since": "warning", "has attachments": "info"}
    word = lambda h: ("%s (%s)" % (SM_WORD.get(h.get("result"), h.get("result")), h.get("status") or "-")) if (h or {}).get("result") else "no review"
    rows = [[i.get("host", ""), i.get("asset", ""), i.get("v", "") or i.get("ruleId", ""), word(i.get("was") or {}),
             word(i.get("before") or {}) if "before" in i else "-", "%s (%s)" % (SM_WORD.get(i.get("result"), i.get("result")), i.get("status")),
             i.get("state", ""), i.get("why", "")] for i in items]
    title = ("STIG Manager update (Check): %d review(s) would be written" % n["would write"]) if check else (
        "STIG Manager update: %d review(s) written" % n["written"])
    if bad:
        title += ", %d not" % bad
    sections = []
    if error:
        sections.append({"title": "Nothing written", "status": "critical", "lines": [error]})
    if rows:
        sections.append({"title": "Each review of the plan", "status": status,
                         "columns": ["Host", "Asset", "Rule", "Read by the check", "STIG Manager before", "Written as", "Result", "Why"],
                         "rows": rows, "row_status": ["critical" if i.get("state") in UPDATE_BAD else chip.get(i.get("state"), "") for i in items],
                         "text": "Only the reviews the approval allowed (the check's plan). Each was read again first: one changed in STIG "
                                 "Manager since the check is left as it is, and so is one with attachments (evidence a person "
                                 "attached stays with its result). A written review was read back. STIG Manager keeps each "
                                 "review's history: what it was is there too. No collection, asset, user or other review was changed."})
    elif not error:
        sections.append({"title": "Nothing to write", "status": "ok", "lines": ["The check's plan has no review (%d)." % planned]})
    return {"title": title, "status": status, "subtitle": "The reviews approved in workflow 37 (the STIG recheck)",
            "summary": [{"label": "Written" if not check else "Would write", "value": n["would write"] if check else n["written"], "status": "ok"},
                        {"label": "Already so", "value": n["already so"]},
                        {"label": "Changed since the check", "value": n["changed since"], "status": "warning" if n["changed since"] else "ok"},
                        {"label": "Has attachments (left)", "value": n["has attachments"]},
                        {"label": "Not written", "value": bad, "status": "critical" if bad else "ok"}],
            "sections": sections,
            "footer": "STIG Manager: GET and POST of these reviews only (playbooks/stigman_update.yml)."}

class FilterModule(object):
    def filters(self):
        return {"stig_compare": stig_compare, "stig_report_sections": stig_report_sections, "stig_lines": stig_lines,
                "stig_findings": stig_findings, "stig_recheck_vulns": stig_recheck_vulns, "stig_recheck": stig_recheck,
                "stig_recheck_sections": stig_recheck_sections, "stig_recheck_lines": stig_recheck_lines,
                "stig_update_plan": stig_update_plan, "stig_update_sections": stig_update_sections,
                "stig_update_report": stig_update_report, "stig_update_attachments": stig_update_attachments, "stig_recheck_headline": recheck_headline,
                "stig_answers_why": answers_why, "stig_answers_used": answers_used}
