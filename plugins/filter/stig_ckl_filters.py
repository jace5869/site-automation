# -*- coding: utf-8 -*-
"""Workflow 40's check, the checklist import (playbooks/stigman_ckl_import.yml): site_ckl_import's answer in lines and as
the report; which files are done with (stig_ckl_settled), to be moved. The plan's own form is stig_update_plan's
(stig_filters.py) - playbooks/stigman_update.yml writes it, or the import itself with stigman_ckl_import_auto."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

SM_WORD = {"pass": "Not a Finding", "fail": "Open", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed"}
CAT = {"high": "CAT I", "medium": "CAT II", "low": "CAT III"}
STATE_CHIP = {"planned": "warning", "nothing to write": "ok", "written": "ok", "not matched": "critical", "no asset": "critical",
              "no STIG on the asset": "critical", "error": "critical", "STIG not on this asset": "info", "in staging too": "info",
              "no grant": "critical"}
LEFT_WORD = {"accepted": "Accepted in STIG Manager", "conflict": "Checklists disagree", "revision": "Another revision of the rule",
             "unmatched": "Not in the STIG", "no_asset": "No asset", "no_stig": "STIG not on the asset", "error": "Error", "grant": "No grant"}
# answers not written that running again does not change: a file with only these (and what is written) is done with
SETTLED_LEFT = ("same", "accepted", "unmatched", "revision")
# what became of a review written at once (auto) that leaves its file done with
DONE = ("written", "already so", "has attachments")
UPDATE_BAD = ("rejected", "error", "bad", "not as written", "no grant")
AUTO_WORD = {"existing": "the assets that are there", "all": "every asset, the new ones too"}
# what became of a move or an assignment written at once that leaves its file done with (1.26.0)
FIRST_DONE = ("moved", "there from an earlier run", "assigned", "already so")
MOVE_KEEPS = ("A move keeps the asset's reviews and their history, its STIGs, metadata and labels; a Restricted grant in the "
              "staging collection that reached it would lose it - such an asset is not moved, a person decides.")


def _where(c):
    return "%s / %s" % (c.get("collection") or "-", c.get("asset") or "-") if c.get("collection") else (c.get("asset") or "-")


def _auto_items(auto_plan, su):
    """The auto write's items, each with its plan item (file, files, collection, benchmark)."""
    plan = list(auto_plan or [])
    out = []
    for i in (su or {}).get("items") or []:
        n = i.get("n")
        it = plan[n] if isinstance(n, int) and 0 <= n < len(plan) else {}
        out.append(dict(i, plan=it))
    return out


def stig_ckl_settled(ci, waiting=None, auto_plan=None, su=None, att=None, att_error=""):
    """Each file read -> {name, sha256, move (true: done with - every answer of it is in STIG Manager, written now, or left for
    good: Accepted, not in the STIG, another revision, with attachments), why (what holds it: read or matched not, waits for
    the approval, not written, checklists that disagree)}."""
    ci = ci or {}
    hold = {}

    def h(name, why):
        if name:
            hold.setdefault(name, why)
    every = ""
    if ci.get("error"):
        every = "nothing planned: " + ci["error"]
    elif att_error:
        every = "the reviews' attachments could not be read: " + att_error
    elif (su or {}).get("error"):
        every = "nothing written: " + su["error"]
    for f in ci.get("files") or []:
        if f.get("error"):
            h(f.get("name"), f["error"])
        elif not f.get("checklists"):
            h(f.get("name"), "no STIG in it")
    for c in ci.get("checklists") or []:
        if STATE_CHIP.get(c.get("state")) == "critical":
            h(c.get("file"), "%s: %s" % (c.get("state"), c.get("why") or "-"))
    for x in ci.get("left") or []:
        if x.get("kind") not in SETTLED_LEFT:
            h(x.get("file"), "%s: %s" % (LEFT_WORD.get(x.get("kind"), x.get("kind")), x.get("why") or "-"))
    for x in ci.get("held") or []:      # a move a person decides: the file stays, so the next run says it again
        for fn in x.get("files") or []:
            h(fn, "the move of %s to %s waits for a person: %s" % (x.get("asset", ""), x.get("to", ""), x.get("why", "")))
    for c in ci.get("checklists") or []:   # a move refused for the grants: the file stays, the next run moves it
        if c.get("move_refused"):
            h(c.get("file"), "not moved to its route's collection: %s" % c["move_refused"])
    for it in waiting or []:
        for fn in it.get("files") or [it.get("file")]:
            h(fn, "waits for the approval (%s)" % ("new asset " + it.get("asset", "") if it.get("kind") == "create" else it.get("line", "")))
    for x in (att or {}).get("left") or []:
        if (x.get("attachments") or 0) < 0:
            for fn in x.get("files") or [x.get("file")]:
                h(fn, "its review's attachments could not be read")
    for i in _auto_items(auto_plan, su):
        ok = i.get("state") == "created" if i.get("kind") == "create" else \
            i.get("state") in FIRST_DONE if i.get("kind") in FIRST else i.get("state") in DONE
        if not ok:
            for fn in i["plan"].get("files") or [i["plan"].get("file")]:
                h(fn, "%s: %s - %s" % (i.get("state", "not written"), i.get("line", ""), i.get("why") or "-"))
    if auto_plan and not (su or {}).get("items") and not (su or {}).get("error"):
        for it in auto_plan:            # the auto write did not run
            for fn in it.get("files") or [it.get("file")]:
                h(fn, "not written")
    out = []
    for f in ci.get("files") or []:
        why = every or hold.get(f.get("name"), "")
        out.append({"name": f.get("name", ""), "sha256": f.get("sha256", ""), "move": not why, "why": why})
    return out


FIRST = ("move", "assign")         # plan items done before reviews (1.26.0): not reviews, no attachments to read


def _unchecked(p):
    """A review the attachments check leaves to the update: a move or an assignment (not a review), or a review of a STIG
    not assigned to its asset yet (assign_stig: nothing can be read there now - the update reads it again before it writes)."""
    return p.get("kind") in FIRST or bool(p.get("assign_stig"))


def stig_ckl_for_attachments(plan):
    """The plan's reviews for the attachments check (check_stig's update_attachments.yml): no move or assignment, no review
    of a STIG being assigned, and a moving asset's review read where it is now (readFrom: the staging collection) -
    stig_ckl_after_attachments puts it back."""
    out = []
    for p in plan or []:
        if _unchecked(p):
            continue
        out.append(dict(p, collectionId=p["readFrom"], moveTo=p.get("collectionId")) if p.get("readFrom") else p)
    return out


def stig_ckl_after_attachments(plan, whole=None, error=""):
    """After the attachments check: the moves and assignments first, then the reviews it kept (each moving asset's to its
    destination again) and those it was not given; the check not done (error): nothing goes on."""
    if error:
        return []
    back = [dict((k, v) for k, v in p.items() if k != "moveTo") if "moveTo" in p else p for p in plan or []]
    back = [dict(p, collectionId=q["moveTo"]) if "moveTo" in q else p for p, q in zip(back, plan or [])]
    whole = list(whole or [])
    return [p for p in whole if p.get("kind") in FIRST] + back + [p for p in whole if _unchecked(p) and p.get("kind") not in FIRST]


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


def stig_ckl_move_lines(moved, settled=None, error=""):
    """The files moved, and left, in lines: 'moved - web01/a.ckl - imported/2026-11-06/web01/a.ckl', 'error - b.ckl - why',
    'c.ckl - LEFT: waits for the approval (...)'."""
    out = [error] if error else []
    for m in moved or []:
        out.append("%s - %s - %s" % (m.get("state", ""), m.get("name", ""), m.get("to") if m.get("state") in ("moved", "would move") else m.get("why", "")))
    out += ["%s - LEFT: %s" % (f.get("name", ""), f.get("why", "")) for f in settled or [] if not f.get("move")]
    return out or ["no file to move"]


def stig_ckl_lines(checklists, many=False):
    """Each checklist's STIG in a line: 'web01/apache.ckl: VMware Aria Operations 8.x Apache Profile -> vrops01 (new) VMW_..._STIG V1R1
    (41 of 41 by version): 12 to write - planned'; many (several collections searched): 'Ops/vrops01'."""
    out = []
    for c in checklists or []:
        out.append("%s: %s -> %s%s%s: %d to write - %s%s" % (
            c.get("file", ""), c.get("stig") or c.get("title") or "?",
            ("%s/%s" % (c.get("collection"), c.get("asset")) if many and c.get("asset") else c.get("asset")) or "(no host name)",
            " (new)" if c.get("new") else "",
            (" %s %s%s" % (c["benchmark"], c.get("revision") or "", (" (%s by %s)" % (c["match"], c.get("by") or "-")) if c.get("match") else ""))
            if c.get("benchmark") else "", c.get("planned", 0), c.get("state", ""), (" - " + c["why"]) if c.get("why") else "")
            + ((" [%s]" % c["route"]) if c.get("route") else "") + ((" [moved from %s first]" % c["moved_from"]) if c.get("moving") else "")
            + ((" [%s assigned first]" % c["assigning"]) if c.get("assigning") else ""))
    return out


def _plan_section(plan, workflow, list_max, auto="none"):
    revs = [p for p in plan if p.get("kind") not in ("create",) + FIRST]
    news = [p for p in plan if p.get("kind") == "create"]
    moves = [p for p in plan if p.get("kind") == "move"]
    assigns = [p for p in plan if p.get("kind") == "assign"]
    if not plan and auto != "none":
        return {"title": "Nothing waits for an approval", "status": "ok", "top": True,
                "lines": ["stigman_ckl_import_auto: %s - the reviews of %s are written at once (above)%s." % (
                    auto, AUTO_WORD.get(auto, auto), "; a new asset and its reviews wait for the approval - none this time" if auto == "existing" else "")]}
    if not plan:
        return {"title": "Nothing to write to STIG Manager", "status": "ok", "top": True,
                "lines": ["Every answer of the checklists is in STIG Manager already, or listed below with why it is not written."
                          + (" The approval step is not asked for." if workflow else "")]}
    rows = [["NEW ASSET", "%s / %s" % (p.get("collection", ""), p.get("asset", "")), ", ".join(p.get("benchmarks") or [p.get("benchmark", "")]),
             "-", "-", "%s%s, then its %d review(s): Not a Finding %d, Open %d, Not Applicable %d" % (
                 p.get("asset", ""), (" - FQDN " + p["fqdn"]) if p.get("fqdn") else "", p.get("reviews", 0),
                 (p.get("counts") or {}).get("pass", 0), (p.get("counts") or {}).get("fail", 0), (p.get("counts") or {}).get("notapplicable", 0)),
             "no asset", "created", "", ""] for p in news]
    rows += [["MOVE", "%s / %s" % (p.get("collection", ""), p.get("asset", "")), ", ".join(p.get("stigs") or []) or "-", "-", "-",
              "from %s (route %s), then its %d review(s)%s" % (p.get("from", ""), p.get("route", "?"), p.get("reviews", 0),
                                                              ("; " + "; ".join(p["hidden"])) if p.get("hidden") else ""),
              "in %s" % p.get("from", ""), "moved", "", ""] for p in moves]
    rows += [["ASSIGN", "%s / %s" % (p.get("collection", ""), p.get("asset", "")), "%s %s" % (p.get("benchmark", ""), p.get("revision", "")),
              "-", "-", "the STIG assigned to it, then its %d review(s)" % p.get("reviews", 0), "not on the asset", "assigned", "", ""] for p in assigns]
    rows += [[p.get("file", ""), "%s / %s%s" % (p.get("collection", ""), p.get("asset", ""), " (moved first)" if p.get("moved_asset") else ""),
              "%s %s" % (p.get("benchmark", ""), p.get("revision", "")),
              p.get("version") or p.get("v", ""), CAT.get(p.get("severity"), p.get("severity", "")), p.get("title", ""),
              ("%s (%s)" % (SM_WORD.get(p["was"]["result"], p["was"]["result"]), p["was"].get("status") or "-")) if (p.get("was") or {}).get("result")
              else "no review", SM_WORD.get(p.get("result"), p.get("result")), p.get("status", ""),
              (p.get("engine") or {}).get("product") or "a person"] for p in revs if not p.get("new_asset")]
    newrevs = len([p for p in revs if p.get("new_asset")])
    title = ("Approve writes these reviews to STIG Manager" if workflow else "To write to STIG Manager (workflow 40, after an approval)")
    if news:
        title += " and creates %d asset%s" % (len(news), "" if len(news) == 1 else "s")
    if moves:
        title += "%s moves %d asset%s" % (" and" if not news else ",", len(moves), "" if len(moves) == 1 else "s")
    if assigns:
        title += "%s assigns %d STIG%s" % (" and" if not (news or moves) else ",", len(assigns), "" if len(assigns) == 1 else "s")
    return {"title": title, "status": "warning", "top": True,
            "columns": ["Checklist", "Collection / asset", "STIG", "Rule", "CAT", "Title", "STIG Manager now", "Written as", "Status", "Answered by"],
            "rows": rows[:list_max], "row_status": ["info"] * len(rows[:list_max]),
            "text": ("The approval step's next job writes exactly these reviews - nothing else: no collection, user, STIG or other "
                     "review is changed, and no asset but the new ones (NEW ASSET: created with that name and every STIG its "
                     "checklists matched, then the checklists' answers written as its reviews - %d of them, counted in its row; the "
                     "update does not create one that is there by then). Each review is written only if it is still as it was read "
                     "here (STIG Manager now); one changed since is left, and said. The detail is the checklist's finding details, "
                     "the comment its comments and where it came from. Not Reviewed in a checklist is never written." % newrevs)
                    + (" MOVE: an asset only in the staging collection, moved to its route's collection first - and only if it is "
                       "still as it was read; its reviews are written there after it, none if it is not moved. " + MOVE_KEEPS if moves else "")
                    + (" ASSIGN: the STIG its checklist matched, assigned to the asset first (stigman_ckl_import_assign_stigs); its "
                       "reviews after it." if assigns else "")
                    + (" The first %d of %d (stigman_ckl_import_list_max)." % (list_max, len(rows)) if len(rows) > list_max else "")}


def _not_done(i):
    """An item written at once that was not done: a review not written, an asset not created, a move or assignment not done."""
    if i.get("kind") == "create":
        return i.get("state") not in ("created", "would create")
    if i.get("kind") in FIRST:
        return i.get("state") not in FIRST_DONE + ("would move", "would assign")
    return i.get("state") in UPDATE_BAD + ("asset not moved", "STIG not assigned")


def _written_section(items, auto, check, list_max):
    bad = [i for i in items if _not_done(i)]
    rows = [[{"create": "NEW ASSET", "move": "MOVE", "assign": "ASSIGN"}.get(i.get("kind")) or i["plan"].get("file", ""), _where(dict(i["plan"], asset=i["plan"].get("asset") or i.get("asset"))),
             i.get("v", "") or i["plan"].get("version", ""), SM_WORD.get(i.get("result"), i.get("result") or "-"), i.get("state", ""), i.get("why", "")]
            for i in items if i.get("kind") == "create" or not i.get("new_asset") or i.get("state") in UPDATE_BAD]
    n = dict((k, len([i for i in items if i.get("kind") not in ("create",) + FIRST and i.get("state") == k])) for k in ("written", "would write", "already so"))
    return {"title": ("Would be written at once (Check): %d review(s)" % n["would write"]) if check else
                     "Written at once, no approval: %d review(s)%s" % (n["written"], (", %d not" % len(bad)) if bad else ""),
            "status": "critical" if bad else "ok", "top": True,
            "columns": ["Checklist", "Collection / asset", "Rule", "Written as", "Result", "Why"], "rows": rows[:list_max],
            "row_status": ["critical" if _not_done(i) else "ok" for i in items
                           if i.get("kind") == "create" or not i.get("new_asset") or i.get("state") in UPDATE_BAD][:list_max],
            "text": "stigman_ckl_import_auto: %s - the reviews of %s written by this job, as STIG Manager - update writes them: each read "
                    "again first and left when it changed since, read back after; never over Accepted, never with attachments. A new "
                    "asset's reviews are counted in its row.%s" % (auto, AUTO_WORD.get(auto, auto),
                                                                   " The first %d of %d." % (list_max, len(rows)) if len(rows) > list_max else "")}


def _moved_section(moved, settled, move_to, list_max):
    moved = list(moved or [])
    kept = [f for f in settled or [] if not f.get("move")]
    rows = [[m.get("name", ""), m.get("to") or "-", m.get("state", ""), m.get("why", "")] for m in moved] + \
           [[f.get("name", ""), "-", "left", f.get("why", "")] for f in kept]
    bad = [m for m in moved if m.get("state") not in ("moved", "would move")]
    return {"title": "Files: %d moved to %s, %d left" % (len([m for m in moved if m.get("state") in ("moved", "would move")]), move_to,
                                                        len(kept) + len(bad)),
            "status": "warning" if bad else "ok", "columns": ["File", "Moved to", "", "Why"], "rows": rows[:list_max],
            "text": "A file is moved (stigman_ckl_import_move) once all of it is in STIG Manager - written, the same already, or left "
                    "for good (Accepted, not in the STIG, another revision, attachments) - and only if it was not written again since "
                    "it was read. One that waits for the approval, was not written, or could not be matched stays, and the next run "
                    "takes it again. %s is never read." % move_to}


def stig_ckl_report(ci, plan=None, sections=None, workflow=False, list_max=200, auto="none", auto_plan=None, su=None, check=False,
                    moved=None, settled=None, move_to=""):
    """The check's report: what was written at once (auto), what Approve writes, each checklist (its asset, the STIG it matched),
    what is not written and why, the files moved."""
    ci = ci or {}
    plan = list(plan if plan is not None else ci.get("plan") or [])
    wrote = _auto_items(auto_plan, su) if auto_plan else []
    done = {}
    for i in wrote:
        if i.get("kind") != "create" and i.get("state") in ("written", "already so"):
            k = (i["plan"].get("file"), i["plan"].get("collection"), i["plan"].get("asset"), i["plan"].get("benchmark"))
            done[k] = done.get(k, 0) + 1
    cks = ci.get("checklists") or []
    left = ci.get("left") or []
    files = ci.get("files") or []
    col = (ci.get("collection") or {}).get("name") or ""
    news = [p for p in plan if p.get("kind") == "create"]
    moves = [p for p in plan if p.get("kind") == "move"]
    assigns = [p for p in plan if p.get("kind") == "assign"]
    revs = [p for p in plan if p.get("kind") not in ("create",) + FIRST]
    held_moves = ci.get("held") or []
    bad = [c for c in cks if STATE_CHIP.get(c.get("state")) == "critical"]
    bad_files = [f for f in files if f.get("error")]
    same = [x for x in left if x.get("kind") == "same"]
    held = [x for x in left if x.get("kind") != "same"]
    wbad = [i for i in wrote if _not_done(i)]
    nw = len([i for i in wrote if i.get("kind") not in ("create",) + FIRST and i.get("state") in ("written", "would write")])
    status = "critical" if (ci.get("error") or (su or {}).get("error") or wbad) else "warning" if (plan or bad or bad_files or held) else "ok"
    if ci.get("error"):
        title = "Checklist import: nothing planned - %s" % ci["error"][:120]
    elif auto_plan:
        title = "Checklist import: %d review(s) %s" % (nw, "would be written" if check else "written")
        if wbad or (su or {}).get("error"):
            title += ", %d not" % (len(wbad) or len(auto_plan))
        if plan:
            title += ", %d to approve%s%s%s" % (len(revs), (" with %d new asset(s)" % len(news)) if news else "",
                                                (", %d to move" % len(moves)) if moves else "", (", %d STIG(s) to assign" % len(assigns)) if assigns else "")
        fm = len([i for i in wrote if i.get("kind") == "move" and i.get("state") in ("moved", "would move", "there from an earlier run")])
        fa = len([i for i in wrote if i.get("kind") == "assign" and i.get("state") in ("assigned", "would assign", "already so")])
        if fm or fa:
            title = title.replace(" written" if not check else " would be written",
                                  "%s%s%s" % (" written" if not check else " would be written", (", %d asset(s) moved" % fm) if fm else "",
                                              (", %d STIG(s) assigned" % fa) if fa else ""), 1)
    elif plan:
        title = "Checklist import: %d review(s) to write%s%s%s" % (len(revs), (", %d new asset(s)" % len(news)) if news else "",
                                                                   (", %d to move" % len(moves)) if moves else "",
                                                                   (", %d STIG(s) to assign" % len(assigns)) if assigns else "")
    else:
        title = "Checklist import: nothing to write"
    if bad:
        title += ", %d checklist STIG(s) not imported" % len(bad)
    if held_moves:
        title += ", %d move(s) for a person" % len(held_moves)
        status = "critical" if status == "critical" else "warning"
    unmoved = [m for m in moved or [] if m.get("state") not in ("moved", "would move")]
    if unmoved:
        title += ", %d file(s) not moved" % len(unmoved)
        status = "critical" if status == "critical" else "warning"
    out = []
    if ci.get("error"):
        out.append({"title": "Nothing planned", "status": "critical", "top": True, "lines": [ci["error"]]})
    else:
        if (su or {}).get("error"):
            out.append({"title": "Nothing written at once", "status": "critical", "top": True, "lines": [su["error"]]})
        elif wrote:
            out.append(_written_section(wrote, auto, check, list_max))
        out.append(_plan_section(plan, workflow, list_max, auto))
    out += list(sections or [])                 # the reviews left out for their attachments (check_stig update_attachments)
    if cks:
        out.append({"title": "Each checklist", "status": "critical" if bad else "ok",
                    "columns": ["Checklist", "Its STIG", "Host name", "Collection / asset", "STIG Manager's STIG", "Rules matched", "Matched by",
                                "NaF / Open / NA / NR", "To write", "State", "Why"],
                    "rows": [[c.get("file", ""), "%s %s" % (c.get("stig") or c.get("title") or "-", c.get("release") or ""), c.get("host") or "-",
                              _where(c) + (" (new)" if c.get("new") else ""), "%s %s" % (c.get("benchmark") or "-", c.get("revision") or ""),
                              c.get("match") or "-", c.get("by") or "-", _counts(c), c.get("planned", 0), _state(c, done),
                              "; ".join(x for x in (c.get("why", ""), ("Route: " + c["route"]) if c.get("route") else "") if x)]
                             for c in cks[:list_max]],
                    "row_status": [STATE_CHIP.get(_state(c, done), "") for c in cks[:list_max]],
                    "text": "Each STIG of each checklist, in each collection that has its asset: the checklist's host name (HOST_NAME), (new): none there, "
                            "created once approved - and the STIG installed in STIG Manager whose rules it matched (by the benchmark "
                            "id, the rule ids, V-numbers or versions; stigman_ckl_import_benchmarks names one). Collections searched: %s; "
                            "a new asset is created in %s. NaF / Open / NA / NR: the checklist's answers." % (
                                ", ".join(ci.get("collections") or [col or "-"]), col or "the collection")})
    if held_moves:
        out.append({"title": "Not moved - a person decides (%d)" % len(held_moves), "status": "warning",
                    "columns": ["Asset", "In", "Its route", "Would lose it", "What to do"],
                    "rows": [[x.get("asset", ""), x.get("from", ""), "%s (route %s)" % (x.get("to", ""), x.get("route", "?")),
                              ", ".join(x.get("who") or []), "Move it in STIG Manager yourself, or take it out of that Restricted grant - the "
                              "next run moves it then."] for x in held_moves[:list_max]],
                    "text": MOVE_KEEPS + " These assets stay in the staging collection; their reviews are written there. Their "
                            "checklist files are not moved (stigman_ckl_import_move), so each run lists them again."})
    if ci.get("routes"):
        rts = ci["routes"]
        out.append({"title": "Routes (%d)" % len(rts), "status": "critical" if any(r.get("error") for r in rts) else "ok",
                    "columns": ["Rule", "Collection", "Fits", "Problem"],
                    "rows": [[str(r.get("n", "")), r.get("want", ""), r.get("text", ""), r.get("error") or "-"] for r in rts],
                    "text": "stigman_ckl_import_routes, in order: the first rule that fits a host wins. A host no rule fits goes to "
                            "%s. An asset already in another collection is written there and never moved." % (col or "the staging collection")})
    if held:
        out.append({"title": "Not written (%d)" % len(held), "status": "warning",
                    "columns": ["Checklist", "Asset", "STIG", "Rule", "Title", "The checklist says", "Not written", "Why"],
                    "rows": [[x.get("file", ""), _where(x), x.get("benchmark", ""), x.get("version") or x.get("v", ""), x.get("title", ""),
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
    if moved is not None and settled is not None:
        out.append(_moved_section(moved, settled, move_to or "imported", list_max))
    return {"title": title, "status": status,
            "subtitle": "Workflow 40's check: the checklists on %s, for %s" % (ci.get("source") or "the share", col or "STIG Manager"),
            "summary": [{"label": "Checklists", "value": len(cks), "note": "STIGs in %d file(s)" % len(files)}]
                       + ([{"label": "Written" if not check else "Would write", "value": nw, "status": "critical" if wbad else "ok",
                            "note": "no approval (%s)" % auto}] if auto_plan else []) + [
                        {"label": "Reviews to write" if not auto_plan else "To approve", "value": len(revs), "status": "warning" if revs else "ok"},
                        {"label": "New assets", "value": len(news), "status": "warning" if news else "ok"}]
                       + ([{"label": "To move", "value": len(moves), "status": "warning",
                            "note": ("%d for a person" % len(held_moves)) if held_moves else "out of %s" % (col or "staging")}]
                          if moves or held_moves else []) + [
                        {"label": "Already so", "value": len(same), "status": "ok"},
                        {"label": "Not written", "value": len(held), "status": "warning" if held else "ok"},
                        {"label": "Not imported", "value": len(bad) + len(bad_files), "status": "critical" if (bad or bad_files) else "ok",
                         "note": "checklists and files"}],
            "sections": out,
            "footer": ("STIG Manager: GET%s; the share: %s (playbooks/stigman_ckl_import.yml). playbooks/stigman_update.yml writes what waits "
                       "for workflow 40's approval." % (", and the reviews and assets written at once (stigman_ckl_import_auto)" if auto_plan else "",
                                                       "read, the files done with moved to %s" % (move_to or "imported") if moved else "only read"))}


def _state(c, done):
    """A checklist's state, as the report shows it: planned and written at once - written."""
    if c.get("state") == "planned" and done.get((c.get("file"), c.get("collection"), c.get("asset"), c.get("benchmark")), 0) >= c.get("planned", 0) > 0:
        return "written"
    return c.get("state", "")


class FilterModule(object):
    def filters(self):
        return {"stig_ckl_file_lines": stig_ckl_file_lines, "stig_ckl_lines": stig_ckl_lines, "stig_ckl_report": stig_ckl_report,
                "stig_ckl_settled": stig_ckl_settled, "stig_ckl_move_lines": stig_ckl_move_lines,
                "stig_ckl_for_attachments": stig_ckl_for_attachments, "stig_ckl_after_attachments": stig_ckl_after_attachments}
