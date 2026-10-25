# -*- coding: utf-8 -*-
"""STIG drift (roles/check_stig, playbooks/stig_drift_report.yml): a host's STIG scan (site_stig_scan) compared with
its reviews in STIG Manager (site_stigman_read) and with the host's last scan kept on the host.

  drift        the scan fails a rule STIG Manager has as Not a Finding (or Not Applicable): the host moved away
               from its checklist - a finding (CAT I critical, CAT II / III warning: check_stig_severity)
  new          the scan fails a rule that passed in the host's last scan (and STIG Manager does not already have
               it open) - a finding
  fixed_open   the scan passes a rule STIG Manager still has Open: the checklist is behind - report only
  unreviewed   the scan fails a rule STIG Manager has no review for - report only
STIG Manager is never written to."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
SCAN_WORD = {"pass": "pass", "fail": "FAIL", "notapplicable": "n/a", "notchecked": "not checked", "error": "error",
             "unknown": "unknown", "informational": "info", "fixed": "fixed"}
SM_WORD = {"pass": "Not a Finding", "fail": "Open", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed",
           "informational": "Informational"}
DEFAULT_SEVERITY = {"high": "critical", "medium": "warning", "low": "warning"}
STIGMAN_ACCEPT = ["saved", "submitted", "accepted"]


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


def stig_compare(scan, stigman=None, host="", severity=None, statuses=None, stigman_used=False, missing_severity="warning"):
    """One host: its scan (site_stig_scan's result), its STIG Manager record (site_stigman_read's hosts[host], {}
    when STIG Manager is not used) -> {host, scanned, ..., drift, new, fixed_open, unreviewed, findings, row}."""
    scan = scan or {}
    sm = stigman or {}
    sev_map = dict(DEFAULT_SEVERITY, **(severity or {}))
    ok_status = statuses if statuses is not None else STIGMAN_ACCEPT
    out = {"host": host, "scanned": bool(scan.get("scanned")), "why": scan.get("why", ""), "os": scan.get("os", ""),
           "stig_version": scan.get("stig_version", ""), "ssg_version": scan.get("ssg_version", ""),
           "profile": scan.get("profile_title") or scan.get("profile", ""), "counts": scan.get("counts") or {},
           "saved": scan.get("saved") or {}, "stigman": {k: sm.get(k, "") for k in ("status", "why", "collection", "asset", "revision")},
           "previous_time": (scan.get("previous") or {}).get("time", ""),
           "drift": [], "new": [], "fixed_open": [], "unreviewed": [], "fixed": [], "findings": []}
    if not out["scanned"]:
        out["findings"].append({"check": "stig", "id": "stig:not-scanned", "severity": "warning",
                                "summary": "the STIG scan did not run: " + (out["why"] or "unknown reason"),
                                "hint": "install openscap-scanner and scap-security-guide (or check_stig_install: true); docs/STIG_DRIFT.md"})
        out["row"] = stig_host_row(out)
        return out
    prev_items = (scan.get("previous") or {}).get("items") or {}
    reviews = sm.get("reviews") or {} if sm.get("status") == "found" else {}
    for it in scan.get("items") or []:
        key, now = it["key"], it.get("result")
        smr = reviews.get(key)
        sm_res = smr["result"] if smr and smr.get("status") in ok_status else None
        prev = prev_items.get(key)
        rec = dict(it, sm=sm_res, sm_status=(smr or {}).get("status", ""), sm_ts=(smr or {}).get("ts", ""),
                   prev=prev, title=_title(it, smr))
        if now == "fail" and sm_res in ("pass", "notapplicable"):
            out["drift"].append(rec)
        elif now == "fail" and prev == "pass" and sm_res != "fail":
            out["new"].append(rec)
        if now == "fail" and sm.get("status") == "found" and sm_res is None and key.startswith("SV-"):
            out["unreviewed"].append(rec)
        if now == "pass" and sm_res == "fail":
            out["fixed_open"].append(rec)
        elif now == "pass" and prev == "fail":
            out["fixed"].append(rec)
    by_sev = lambda r: (SEV_ORDER.get(r.get("severity"), 3), r["key"])
    for k in ("drift", "new", "fixed_open", "unreviewed", "fixed"):
        out[k].sort(key=by_sev)
    report = out["saved"].get("report") or ""
    if out["drift"]:
        out["findings"].append({"check": "stig", "id": "stig:drift", "severity": _worst(out["drift"], sev_map),
                                "summary": "%d STIG rule%s STIG Manager has as Not a Finding or Not Applicable fail%s now: %s" % (
                                    len(out["drift"]), "" if len(out["drift"]) == 1 else "s", "s" if len(out["drift"]) == 1 else "", _short_list(out["drift"])),
                                "hint": "fix the host, or - if the change was approved - update its review in STIG Manager"
                                        + ("; the scan's report on the host: " + report if report else "")})
    if out["new"]:
        out["findings"].append({"check": "stig", "id": "stig:new", "severity": _worst(out["new"], sev_map),
                                "summary": "%d STIG rule%s passed in the last scan (%s) and fail%s now: %s" % (
                                    len(out["new"]), "" if len(out["new"]) == 1 else "s", out["previous_time"] or "-",
                                    "s" if len(out["new"]) == 1 else "", _short_list(out["new"])),
                                "hint": "what changed on the host since that scan" + ("; the scan's report on the host: " + report if report else "")})
    if stigman_used and sm.get("status") != "found" and missing_severity != "none":
        out["findings"].append({"check": "stig", "id": "stig:stigman", "severity": missing_severity,
                                "summary": "not compared with STIG Manager: " + (sm.get("why") or "not read"),
                                "hint": "the asset's name and collection: check_stig_stigman_asset, check_stig_stigman_collection; docs/STIG_DRIFT.md"})
    out["row"] = stig_host_row(out)
    return out


def stig_host_row(c):
    """The every-host table's row and its status."""
    cnt = c.get("counts") or {}
    sm = c.get("stigman") or {}
    if not c.get("scanned"):
        return {"cells": [c["host"], c.get("os", ""), "not scanned: " + c.get("why", ""), "", "", "", "", "", "", ""], "status": "warning"}
    if sm.get("status") == "found":
        smtxt = "%s / %s%s" % (sm.get("collection", ""), sm.get("asset", ""), (" (" + sm["revision"] + ")") if sm.get("revision") else "")
    elif sm.get("status"):
        smtxt = "not compared: " + sm.get("why", "")
    else:
        smtxt = "not used"
    status = "critical" if any(f["severity"] == "critical" for f in c["findings"]) else (
        "warning" if c["findings"] else "ok")
    return {"cells": [c["host"], c.get("os", ""), "%s %s" % (c.get("stig_version", ""), ("(SSG " + c["ssg_version"] + ")") if c.get("ssg_version") else ""),
                      smtxt, str(cnt.get("pass", 0)), str(cnt.get("fail", 0)), str(len(c["drift"])), str(len(c["new"])),
                      str(len(c["fixed_open"])), (c.get("saved") or {}).get("report", "") or "(not kept: dry run)"],
            "status": status}


def _rows(results, key, cols):
    rows, rs = [], []
    for c in results:
        for r in c.get(key) or []:
            rows.append(cols(c, r))
            rs.append({"high": "critical"}.get(r.get("severity"), "warning"))
    return rows, rs


def stig_report_sections(results, list_max=200):
    """Every host's stig_compare result -> the report's sections: what moved (drift, new fails), what the checklist
    is behind on, then every host, then the fails STIG Manager has no review for."""
    results = [r for r in (results or []) if r]
    sections = []
    drift_rows, drift_rs = _rows(results, "drift", lambda c, r: [
        c["host"], _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL",
        "%s%s" % (SM_WORD.get(r["sm"], r["sm"]), (" (" + r["sm_status"] + ")") if r.get("sm_status") else ""),
        SCAN_WORD.get(r.get("prev"), r.get("prev") or "-")])
    new_rows, new_rs = _rows(results, "new", lambda c, r: [
        c["host"], _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "pass (" + (c.get("previous_time") or "-") + ")", "FAIL",
        SM_WORD.get(r["sm"], "no review") if r.get("sm") else "no review"])
    fo_rows, fo_rs = _rows(results, "fixed_open", lambda c, r: [
        c["host"], _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "pass",
        "Open (%s%s)" % (r.get("sm_status") or "-", (", " + r["sm_ts"][:10]) if r.get("sm_ts") else "")])
    un_rows, un_rs = _rows(results, "unreviewed", lambda c, r: [
        c["host"], _rule_label(r), CAT.get(r.get("severity"), r.get("severity", "")), r["title"], "FAIL"])
    not_scanned = [c for c in results if not c.get("scanned")]
    boxes = [{"label": "Drifted", "value": len(drift_rows), "status": ("critical" if "critical" in drift_rs else "warning") if drift_rows else "ok"},
             {"label": "New fails", "value": len(new_rows), "status": ("critical" if "critical" in new_rs else "warning") if new_rows else "ok"},
             {"label": "Fixed, still open in STIG Manager", "value": len(fo_rows), "status": "info" if fo_rows else "ok"},
             {"label": "Not scanned", "value": len(not_scanned), "status": "warning" if not_scanned else "ok"}]
    cols = ["Host", "STIG rule", "CAT", "Title"]
    if drift_rows:
        sections.append({"title": "1. Drifted - the host fails what STIG Manager has as Not a Finding", "columns": cols + ["Scan now", "STIG Manager", "Last scan"],
                         "rows": drift_rows, "row_status": drift_rs, "status": "critical" if "critical" in drift_rs else "warning",
                         "text": "These hosts moved away from their checklist: fix the host, or - if the change was approved - update the review in STIG Manager "
                                 "(this job never writes to it). CAT I is critical, CAT II and III a warning (check_stig_severity)."})
    if new_rows:
        sections.append({"title": "2. New fails - passed in the host's last scan", "columns": cols + ["Last scan", "Scan now", "STIG Manager"],
                         "rows": new_rows, "row_status": new_rs, "status": "critical" if "critical" in new_rs else "warning",
                         "text": "Something changed on the host since its last scan (the scans are kept on the host, check_stig_dir). "
                                 "STIG Manager has no Not a Finding for them, so they are not drift from the checklist - but they are new."})
    if fo_rows:
        sections.append({"title": "3. Fixed on the host, still Open in STIG Manager - the checklist is behind", "columns": cols + ["Scan now", "STIG Manager"],
                         "rows": fo_rows, "row_status": ["info"] * len(fo_rows), "status": "info",
                         "text": "No finding: the host passes. Update these reviews in STIG Manager (and close their POA&M items)."})
    if not (drift_rows or new_rows) and results:
        sections.append({"title": "No drift", "status": "ok",
                         "lines": ["Every host scanned passes what STIG Manager has as Not a Finding, and nothing that passed in a host's last scan fails now."
                                   + (" %d host%s could not be scanned (below)." % (len(not_scanned), "" if len(not_scanned) == 1 else "s") if not_scanned else "")]})
    for s in sections:
        s["top"] = True
    if sections:
        sections[0]["boxes"] = boxes
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    hosts = sorted(results, key=lambda c: (order.get(c["row"]["status"], 3), c["host"]))
    hrs = [c["row"]["status"] for c in hosts]
    sections.append({"title": "Every host - the scan, STIG Manager, the counts", "status": "critical" if "critical" in hrs else ("warning" if "warning" in hrs else "ok"),
                     "columns": ["Host", "OS", "STIG (scan)", "STIG Manager (collection / asset)", "Pass", "Fail", "Drifted", "New", "Fixed, open", "The report on the host"],
                     "rows": [c["row"]["cells"] for c in hosts], "row_status": hrs,
                     "text": "Pass and Fail count STIG rules (several SCAP checks of one rule count once); the rest are not applicable or not checked "
                             "automatically. Each scan's HTML report, results and summary stay on the host."})
    if un_rows:
        shown = un_rows[:list_max]
        sections.append({"title": "Fails STIG Manager has no review for", "columns": cols + ["Scan now"], "rows": shown, "row_status": un_rs[:list_max], "status": "info",
                         "text": "Report only: the checklist has no result for these rules yet."
                                 + (" The first %d of %d (check_stig_list_max)." % (list_max, len(un_rows)) if len(un_rows) > list_max else "")})
    return sections


def stig_lines(c):
    """One host's result in plain lines, for the job's output."""
    if not c.get("scanned"):
        return ["%s: NOT SCANNED - %s" % (c["host"], c.get("why", ""))]
    cnt = c.get("counts") or {}
    sm = c.get("stigman") or {}
    out = ["%s: %s %s - %d pass, %d fail; drifted %d, new %d, fixed but open %d; STIG Manager: %s" % (
        c["host"], c.get("profile", ""), c.get("stig_version", ""), cnt.get("pass", 0), cnt.get("fail", 0), len(c["drift"]), len(c["new"]),
        len(c["fixed_open"]), ("%s / %s%s" % (sm.get("collection"), sm.get("asset"), (" (" + sm["revision"] + ")") if sm.get("revision") else ""))
        if sm.get("status") == "found" else (sm.get("why") or "not used"))]
    for r in c["drift"]:
        out.append("  DRIFT %s %s %s - STIG Manager: %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90], SM_WORD.get(r["sm"], r["sm"])))
    for r in c["new"]:
        out.append("  NEW   %s %s %s" % (_rule_label(r), CAT.get(r.get("severity"), ""), r["title"][:90]))
    if (c.get("saved") or {}).get("report"):
        out.append("  report on the host: " + c["saved"]["report"])
    elif not c.get("saved"):
        out.append("  nothing kept on the host (not kept: dry run)")
    return out


class FilterModule(object):
    def filters(self):
        return {"stig_compare": stig_compare, "stig_report_sections": stig_report_sections, "stig_lines": stig_lines}
