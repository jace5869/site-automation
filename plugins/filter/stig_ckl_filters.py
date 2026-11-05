# -*- coding: utf-8 -*-
"""Workflow 40's check, the checklist import (playbooks/stigman_ckl_import.yml): site_ckl_import's answer in lines and as
the report. The plan's own form is stig_update_plan's (stig_filters.py) - playbooks/stigman_update.yml writes it."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

SM_WORD = {"pass": "Not a Finding", "fail": "Open", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed"}
CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
STATE_CHIP = {"planned": "warning", "nothing to write": "ok", "not matched": "critical", "no asset": "critical",
              "no STIG on the asset": "critical", "error": "critical"}
LEFT_WORD = {"accepted": "Accepted in STIG Manager", "conflict": "Checklists disagree", "revision": "Another revision of the rule",
             "unmatched": "Not in the STIG", "no_asset": "No asset", "no_stig": "STIG not on the asset", "error": "Error"}


def _counts(c):
    n = c.get("counts") or {}
    return "%d / %d / %d / %d" % (n.get("pass", 0), n.get("fail", 0), n.get("notapplicable", 0), n.get("notchecked", 0))


def stig_ckl_file_lines(ci):
    """The files read, one line each: 'web01/apache.ckl - 2 STIG(s), 41 KB, changed 2026-11-05 10:02 UTC'."""
    ci = ci or {}
    head = "%d file(s) from %s%s" % (len(ci.get("files") or []), ci.get("source") or "the share", (" as " + ci["account"]) if ci.get("account") else "")
    out = [head] + ["%s - %s" % (f.get("name", ""), f["error"] if f.get("error") else "%d STIG(s), %d bytes%s, SHA-256 %s" % (
        f.get("checklists", 0), f.get("size", 0), (", changed " + f["modified"]) if f.get("modified") else "", f.get("sha256", "")))
        for f in ci.get("files") or []]
    if ci.get("error"):
        out.append("NOT READ: " + ci["error"])
    return out


def stig_ckl_lines(checklists):
    """Each checklist's STIG in a line: 'web01/apache.ckl: VMware Aria Operations 8.x Apache Profile -> vrops01 (new) VMW_..._STIG V1R1
    (41 of 41 by version): 12 to write - planned'."""
    out = []
    for c in checklists or []:
        out.append("%s: %s -> %s%s%s: %d to write - %s%s" % (
            c.get("file", ""), c.get("stig") or c.get("title") or "?", c.get("asset") or "(no host name)", " (new)" if c.get("new") else "",
            (" %s %s%s" % (c["benchmark"], c.get("revision") or "", (" (%s by %s)" % (c["match"], c.get("by") or "-")) if c.get("match") else ""))
            if c.get("benchmark") else "", c.get("planned", 0), c.get("state", ""), (" - " + c["why"]) if c.get("why") else ""))
    return out


def _plan_section(plan, workflow, list_max):
    revs = [p for p in plan if p.get("kind") != "create"]
    news = [p for p in plan if p.get("kind") == "create"]
    if not plan:
        return {"title": "Nothing to write to STIG Manager", "status": "ok", "top": True,
                "lines": ["Every answer of the checklists is in STIG Manager already, or listed below with why it is not written."
                          + (" The approval step is not asked for." if workflow else "")]}
    rows = [["NEW ASSET", "%s / %s" % (p.get("collection", ""), p.get("asset", "")), ", ".join(p.get("benchmarks") or [p.get("benchmark", "")]),
             "-", "-", "%s%s, then its %d review(s): Not a Finding %d, Open %d, Not Applicable %d" % (
                 p.get("asset", ""), (" - FQDN " + p["fqdn"]) if p.get("fqdn") else "", p.get("reviews", 0),
                 (p.get("counts") or {}).get("pass", 0), (p.get("counts") or {}).get("fail", 0), (p.get("counts") or {}).get("notapplicable", 0)),
             "no asset", "created", "", ""] for p in news]
    rows += [[p.get("file", ""), "%s / %s" % (p.get("collection", ""), p.get("asset", "")), "%s %s" % (p.get("benchmark", ""), p.get("revision", "")),
              p.get("version") or p.get("v", ""), CAT.get(p.get("severity"), p.get("severity", "")), p.get("title", ""),
              ("%s (%s)" % (SM_WORD.get(p["was"]["result"], p["was"]["result"]), p["was"].get("status") or "-")) if (p.get("was") or {}).get("result")
              else "no review", SM_WORD.get(p.get("result"), p.get("result")), p.get("status", ""),
              (p.get("engine") or {}).get("product") or "a person"] for p in revs if not p.get("new_asset")]
    newrevs = len([p for p in revs if p.get("new_asset")])
    title = ("Approve writes these reviews to STIG Manager" if workflow else "To write to STIG Manager (workflow 40, after an approval)")
    if news:
        title += " and creates %d asset%s" % (len(news), "" if len(news) == 1 else "s")
    return {"title": title, "status": "warning", "top": True,
            "columns": ["Checklist", "Collection / asset", "STIG", "Rule", "CAT", "Title", "STIG Manager now", "Written as", "Status", "Answered by"],
            "rows": rows[:list_max], "row_status": ["info"] * len(rows[:list_max]),
            "text": ("The approval step's next job writes exactly these reviews - nothing else: no collection, user, STIG or other "
                     "review is changed, and no asset but the new ones (NEW ASSET: created with that name and every STIG its "
                     "checklists matched, then the checklists' answers written as its reviews - %d of them, counted in its row; the "
                     "update does not create one that is there by then). Each review is written only if it is still as it was read "
                     "here (STIG Manager now); one changed since is left, and said. The detail is the checklist's finding details, "
                     "the comment its comments and where it came from. Not Reviewed in a checklist is never written." % newrevs)
                    + (" The first %d of %d (stigman_ckl_import_list_max)." % (list_max, len(rows)) if len(rows) > list_max else "")}


def stig_ckl_report(ci, plan=None, sections=None, workflow=False, list_max=200):
    """The check's report: what Approve writes, each checklist (its asset, the STIG it matched), what is not written and why."""
    ci = ci or {}
    plan = list(plan if plan is not None else ci.get("plan") or [])
    cks = ci.get("checklists") or []
    left = ci.get("left") or []
    files = ci.get("files") or []
    col = (ci.get("collection") or {}).get("name") or ""
    news = [p for p in plan if p.get("kind") == "create"]
    revs = [p for p in plan if p.get("kind") != "create"]
    bad = [c for c in cks if STATE_CHIP.get(c.get("state")) == "critical"]
    bad_files = [f for f in files if f.get("error")]
    same = [x for x in left if x.get("kind") == "same"]
    held = [x for x in left if x.get("kind") != "same"]
    status = "critical" if ci.get("error") else "warning" if (plan or bad or bad_files or held) else "ok"
    if ci.get("error"):
        title = "Checklist import: nothing planned - %s" % ci["error"][:120]
    elif plan:
        title = "Checklist import: %d review(s) to write%s" % (len(revs), (", %d new asset(s)" % len(news)) if news else "")
    else:
        title = "Checklist import: nothing to write"
    if bad:
        title += ", %d checklist STIG(s) not imported" % len(bad)
    out = []
    if ci.get("error"):
        out.append({"title": "Nothing planned", "status": "critical", "top": True, "lines": [ci["error"]]})
    else:
        out.append(_plan_section(plan, workflow, list_max))
    out += list(sections or [])                 # the reviews left out for their attachments (check_stig update_attachments)
    if cks:
        out.append({"title": "Each checklist", "status": "critical" if bad else "ok",
                    "columns": ["Checklist", "Its STIG", "Host name", "Asset", "STIG Manager's STIG", "Rules matched", "Matched by",
                                "NaF / Open / NA / NR", "To write", "State", "Why"],
                    "rows": [[c.get("file", ""), "%s %s" % (c.get("stig") or c.get("title") or "-", c.get("release") or ""), c.get("host") or "-",
                              (c.get("asset") or "-") + (" (new)" if c.get("new") else ""), "%s %s" % (c.get("benchmark") or "-", c.get("revision") or ""),
                              c.get("match") or "-", c.get("by") or "-", _counts(c), c.get("planned", 0), c.get("state", ""), c.get("why", "")]
                             for c in cks[:list_max]],
                    "row_status": [STATE_CHIP.get(c.get("state"), "") for c in cks[:list_max]],
                    "text": "Each STIG of each checklist: its asset in %s - the checklist's host name (HOST_NAME), (new): none there, "
                            "created once approved - and the STIG installed in STIG Manager whose rules it matched (by the benchmark "
                            "id, the rule ids, V-numbers or versions; stigman_ckl_import_benchmarks names one). NaF / Open / NA / NR: "
                            "the checklist's answers." % (col or "the collection")})
    if held:
        out.append({"title": "Not written (%d)" % len(held), "status": "warning",
                    "columns": ["Checklist", "Asset", "STIG", "Rule", "Title", "The checklist says", "Not written", "Why"],
                    "rows": [[x.get("file", ""), x.get("asset") or "-", x.get("benchmark", ""), x.get("version") or x.get("v", ""), x.get("title", ""),
                              SM_WORD.get(x.get("result"), x.get("result")), LEFT_WORD.get(x.get("kind"), x.get("kind")), x.get("why", "")]
                             for x in held[:list_max]],
                    "text": "Answers of the checklists that are not handed to the approval, and why: an Accepted review stays for its "
                            "reviewers (stigman_ckl_import_overwrite_accepted), two checklists that disagree leave the rule to a "
                            "person, a rule id of another revision is held (stigman_ckl_import_other_revision), and a checklist "
                            "without its asset or STIG writes nothing."
                            + (" The first %d of %d." % (list_max, len(held)) if len(held) > list_max else "")})
    if same:
        out.append({"title": "Already so in STIG Manager (%d)" % len(same), "status": "ok",
                    "lines": ["%d answer(s) of the checklists are STIG Manager's result already - nothing to write for them." % len(same)]})
    if bad_files:
        out.append({"title": "Files not read (%d)" % len(bad_files), "status": "warning",
                    "columns": ["File", "Why"], "rows": [[f.get("name", ""), f.get("error", "")] for f in bad_files[:list_max]]})
    return {"title": title, "status": status,
            "subtitle": "Workflow 40's check: the checklists on %s, for %s" % (ci.get("source") or "the share", col or "STIG Manager"),
            "summary": [{"label": "Checklists", "value": len(cks), "note": "STIGs in %d file(s)" % len(files)},
                        {"label": "Reviews to write", "value": len(revs), "status": "warning" if revs else "ok"},
                        {"label": "New assets", "value": len(news), "status": "warning" if news else "ok"},
                        {"label": "Already so", "value": len(same), "status": "ok"},
                        {"label": "Not written", "value": len(held), "status": "warning" if held else "ok"},
                        {"label": "Not imported", "value": len(bad) + len(bad_files), "status": "critical" if (bad or bad_files) else "ok",
                         "note": "checklists and files"}],
            "sections": out,
            "footer": "STIG Manager and the share were only read (playbooks/stigman_ckl_import.yml); playbooks/stigman_update.yml writes "
                      "the plan after workflow 40's approval."}


class FilterModule(object):
    def filters(self):
        return {"stig_ckl_file_lines": stig_ckl_file_lines, "stig_ckl_lines": stig_ckl_lines, "stig_ckl_report": stig_ckl_report}
