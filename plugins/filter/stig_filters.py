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
STIG Manager is never written to."""
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


def evaluate_note(scan):
    """What went wrong in Evaluate-STIG's run that the job worked around: its checklist read although its own schema check
    rejected it, its checks that failed ('' = nothing)."""
    out = []
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
        out.append("  DISAGREE %s %s %s - STIG Manager: %s; the scan's check: %s" % (
            _rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90], SM_WORD.get(r["sm"], r["sm"]), ", ".join(r.get("scap") or [])))
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


class FilterModule(object):
    def filters(self):
        return {"stig_compare": stig_compare, "stig_report_sections": stig_report_sections, "stig_lines": stig_lines,
                "stig_findings": stig_findings}
