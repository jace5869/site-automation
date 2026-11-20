# -*- coding: utf-8 -*-
"""Ticket insights: the Automation backlog (1.81.0) - the incident issues ("clusters") ranked by what automating them would
save. Every number is worked out here by code from the report's own tickets (backlog_rows); ACT writes only the words
(a strategy, a summary, a problem statement and, for each of three phases, an approach, what it needs and an AI note) -
one question per cluster for the top servicenow_insights_backlog_issues, asked by backlog_batches, read by backlog_merge,
kept in the report database (backlog_hash is the key: a cluster whose tickets did not change is not asked again).
backlog_docx and backlog_proposal_md write the proposals; docs/SERVICENOW_INSIGHTS.md ("Automation backlog") has the formulas."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import json
import math
import re

_SIB = {}

MONTH_DAYS = 30.4375            # the window's months: days / 30.4375 (1 Jan to 31 Oct = 9.99)
H_TICKET, H_HANDOFF = 0.5, 0.25
W_VOLUME, W_PHASE, W_VIP = 0.5, 0.3, 0.2
CUT_P1, CUT_P2 = 40, 20         # score at or above: P1; at or above: P2; else P3
P3_CAP = 80                    # phase 3 settles at most this share: zero-touch is an estimate, never "everything"
MIN_TICKETS = 5                # clusters with fewer tickets in the window are left out (servicenow_insights_backlog_min_tickets)
# the three phases: name, complexity before the handoff point, effort days per complexity point
PHASES = [("Self-service and KB", 1, 1), ("ServiceNow + PowerShell", 2, 3), ("Zero-touch", 4, 5)]
AI_LABELS = ("Development-Assist", "Runtime-Integrated", "None")
LIMITS = {"strategy": 240, "summary": 300, "problem": 300, "approach": 200, "need": 60, "ai_note": 160}
NEEDS_MAX = 4
OFF_TEXT = "not drafted (ACT off)"
PROMPT_VERSION = "backlog-1"


def _F():
    if "F" not in _SIB:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("servicenow_insights_filters_for_backlog", os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "servicenow_insights_filters.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SIB["F"] = mod
    return _SIB["F"]


def _o(o, k, d):
    v = (o or {}).get(k)
    return d if v is None or v == "" else v


def cluster_label(name):
    """How a cluster is named on the page and in the files: a ticket with no category, or the category Other, is "Not categorised"
    (the cluster keeps its name for the id and the hash)."""
    n = str(name or "")
    if n == "(no category)":
        return "Not categorised"
    return re.sub(r"^(?:\(no category\)|\(none\)|Other)(?= / |$)", "Not categorised", n)


def cluster_id(name):
    """A cluster's id on the page: stable from run to run (the priority a reader sets is kept under it)."""
    return "bk" + hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:8]


def window_months(since, until):
    """The window's length in months, to two decimals' worth: days / 30.4375."""
    try:
        m = (until - since).total_seconds() / 86400.0 / MONTH_DAYS
        return m if m > 0 else 1.0
    except Exception:           # noqa: BLE001 - no window: one month, so nothing divides by zero
        return 1.0


def _fmt(x, nd=0):
    return ("{:,.%df}" % nd).format(x)


def _num(x):
    """1,234 or 12.5: a whole number without a decimal, else one."""
    return _fmt(x, 0) if abs(x - round(x)) < 0.05 else _fmt(x, 1)


def suggest_priority(score):
    return 1 if score >= CUT_P1 else 2 if score >= CUT_P2 else 3


def _phase(i, n, sets, hand_med, impact):
    """One phase's figures. sets: the ticket ids each phase would settle, each a superset of the one before - every ticket is
    counted once. Deflection (the tickets the phase would settle): phase 1 - resolved with a close note that says what was done
    and never passed between groups; phase 2 - every ticket with such a note (a script can do what the note says); phase 3 - those
    and the tickets back on the same CI within repeat_ci_days (an event can start the script), at most P3_CAP percent (an estimate)."""
    name, base, per = PHASES[i]
    settles = len(sets[i])
    if i == 2:
        settles = min(settles, int(n * P3_CAP / 100.0))
    cx = min(5, base + (1 if hand_med >= 2 else 0))
    defl = 100.0 * settles / n if n else 0.0
    return {"i": i + 1, "name": name, "complexity": cx, "impact": impact, "effort": cx * per, "per": per, "settles": settles,
            "defl": int(round(defl)), "score": int(round(defl * impact / (5.0 * cx))) if cx else 0,
            "score_x": (defl * impact / (5.0 * cx)) if cx else 0.0, "capped": i == 2 and len(sets[2]) > settles}


def _ticket_key(it, F, o):
    """One ticket as the cluster's key has it: its id and what the figures read of it (done, handoffs, the grade of its close note)
    - a ticket resolved, passed on or noted since the draft was written gives another key, and the cluster is drafted again."""
    return "%s|%s|%s|%s" % (it.get("sys_id"), it.get("done_at") or "", int(it.get("reassignment_count") or 0),
                           F.grade(it.get("close_notes"), o) if it.get("done_at") else "")


def backlog_rows(tickets, issues_of, since, until, opts=None, vip=None, repeat_days=7.0):
    """The ranked clusters of one scope. tickets: its incidents opened in the window (raw items); issues_of: {sys_id:
    issue}; vip: the report's VIP tiers (vip_tiers) or None. -> {months, total, rows: [cluster ...], p1, saved, top}."""
    F = _F()
    o = opts or {}
    months = window_months(since, until)
    by = {}
    for it in tickets or []:
        n = issues_of.get(it.get("sys_id"))
        if n and n != "Other / unclear":
            by.setdefault(n, []).append(it)
    total = sum(len(v) for v in by.values())
    all_total = len(tickets or [])
    raw = []
    for name, lst in by.items():
        n = len(lst)
        hops = [int(it.get("reassignment_count") or 0) for it in lst]
        hand = sum(hops)
        med = F._median(hops) if hops else 0
        g0, g1, rec = set(), set(), set()
        for it in lst:
            if it.get("done_at") and F.grade(it.get("close_notes"), o) == "good":
                g1.add(it["sys_id"])
                if int(it.get("reassignment_count") or 0) == 0:
                    g0.add(it["sys_id"])
        # back on the same CI within repeat_days: a ticket whose CI had an earlier ticket of this cluster that close
        seen = {}
        for it in sorted(lst, key=lambda x: x.get("opened_at") or ""):
            ci, t = it.get("cmdb_ci"), F._t(it.get("opened_at"))
            if ci and t is not None:
                if ci in seen and (t - seen[ci]).total_seconds() <= repeat_days * 86400:
                    rec.add(it["sys_id"])
                seen[ci] = t
        good, good0 = len(g1), len(g0)
        sets = [g0, g1, g1 | rec]
        nv = 0
        if vip and vip.get("on"):
            nv = sum(1 for it in lst if F.vip_of(it, vip) is not None)
        cats = {}
        for it in lst:
            c = F.category_name(it)
            cats[c] = cats.get(c, 0) + 1
        cat = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        cat = "Not categorised" if cat in ("(none)", "Other") else cat
        ex = sorted(lst, key=lambda it: it.get("opened_at") or "", reverse=True)[:3]
        raw.append({"name": name, "label": cluster_label(name), "cat": cat, "n": n, "hand": hand, "hand_med": med, "good": good, "good0": good0, "rec": len(rec), "sets": sets, "vip": nv,
                    "examples": [x.get("number") for x in ex if x.get("number")], "ids": sorted(_ticket_key(x, F, o) for x in lst if x.get("sys_id"))})
    min_n = int(_o(o, "backlog_min_tickets", MIN_TICKETS))
    left_out = [r for r in raw if r["n"] < min_n]
    raw = [r for r in raw if r["n"] >= min_n]
    top_rate = max([r["n"] for r in raw] or [1]) / months
    rows = []
    for r in raw:
        n, rate = r["n"], r["n"] / months
        impact = max(1, min(5, int(math.ceil(5.0 * rate / top_rate - 1e-9))))
        ph = [_phase(i, n, r["sets"], r["hand_med"], impact) for i in range(3)]
        best = max(range(3), key=lambda i: (round(ph[i]["score_x"], 6), -i)) + 1             # on the unrounded scores: 28.4 beats 28.0
        labor = H_TICKET * n + H_HANDOFF * r["hand"]
        saved = labor / months * 12.0 * ph[best - 1]["defl"] / 100.0
        vol = 100.0 * rate / top_rate
        vipv = min(100.0, 100.0 * r["vip"] / n * 5.0) if n else 0.0
        score = W_VOLUME * vol + W_PHASE * ph[best - 1]["score_x"] + W_VIP * vipv
        r.update({"id": cluster_id(r["name"]), "rate": rate, "share": 100.0 * n / all_total if all_total else 0.0, "labor": labor,
                  "saved": saved, "vol": vol, "vipv": vipv, "score": int(round(score)), "score_x": score, "ph": ph, "best": best,
                  "prio": suggest_priority(int(round(score)))})
        del r["sets"]
        rows.append(r)
    rows.sort(key=lambda r: (-r["score_x"], -r["n"], r["name"]))
    top = rows[0] if rows else None
    return {"months": months, "total": all_total, "in_clusters": total, "rows": rows, "left_out": len(left_out), "left_out_tickets": sum(r["n"] for r in left_out), "min_n": min_n, "p1": sum(1 for r in rows if r["prio"] == 1),
            "saved": sum(r["saved"] for r in rows), "vip_on": bool(vip and vip.get("on")), "top": (top["label"], top["score"], top["rate"], top["best"]) if top else None}


# ---- the hover texts (the formula of each number, with the figures put in) --------------------------------------

def tip_rate(r, bm):
    return "%s tickets ÷ %.1f months = %.1f a month" % (_fmt(r["n"]), bm["months"], r["rate"])


def tip_saved(r, bm):
    b = r["ph"][r["best"] - 1]
    return ("(%s h × %s + %s h × %s handoffs) = %s h in the window; ÷ %.1f × 12 × %d%% (phase %d) = %s h a year" % (
        H_TICKET, _fmt(r["n"]), H_HANDOFF, _fmt(r["hand"]), _fmt(r["labor"]), bm["months"], b["defl"], r["best"], _fmt(r["saved"])))


def tip_score(r, bm):
    b = r["ph"][r["best"] - 1]
    return "%s × %d + %s × %d + %s × %d = %d" % (W_VOLUME, round(r["vol"]), W_PHASE, b["score"], W_VIP, round(r["vipv"]), r["score"])


def tip_phase(p, r):
    return ("Deflection = tickets this phase settles ÷ tickets = %s of %s tickets%s<br>Complexity = %d (base) + 1 if median handoffs ≥ 2<br>"
            "Effort = %d days per complexity point<br>Score = %d%% × %d ÷ (5 × %d) = %d" % (
                _fmt(p["settles"]), _fmt(r["n"]), (" (an estimate, at most %d%%)" % P3_CAP) if p["i"] == 3 else "", PHASES[p["i"] - 1][1], p["per"], p["defl"], p["impact"], p["complexity"], p["score"]))


# ---- ACT's words -------------------------------------------------------------------------------------------------

def backlog_hash(ids, version=PROMPT_VERSION, evidence=""):
    """A cluster's key in the report database: its tickets (sorted: id and what the figures read of each), the prompt's version and the
    evidence text ACT was sent (its numbering left out), as a SHA-256 - the cluster as it was asked about; a ticket more or less, a
    close note or a figure that changed asks again."""
    ev = re.sub(r"^CLUSTER B\d+:", "CLUSTER:", str(evidence or ""), flags=re.M)
    ev = re.sub(r"\b(PERSON|PHONE|EMAIL|HOST|IP|ID)-\d+\b", r"\1-N", ev)           # a mask's numbers differ from run to run: only the kinds count
    return hashlib.sha256((version + "\n" + "\n".join(sorted(str(i) for i in ids)) + "\n--\n" + ev).encode("utf-8")).hexdigest()


def _rows_from(r, state, mask, opts):
    F = _F()
    o = opts or {}
    since, until, _ = F._window(r)
    issues_of = F._assign(r, state, mask or {"pairs": [], "counts": {}}, o)
    inc = [it for it in (r or {}).get("items") or [] if it.get("table") == "incident" and F._in(F._t(it.get("opened_at")), since, until)]
    vip = F.vip_tiers(r, o)
    return backlog_rows(inc, issues_of, since, until, o, vip, float(_o(o, "repeat_ci_days", 7))), inc, issues_of


def backlog_batches(r, state, mask, opts=None):
    """One question per cluster for the top backlog_issues by score: [{items: [{id}], issues: [{id, name, hash, facts,
    notes (masked), shorts (masked), ...}]}] - each its own batch."""
    F = _F()
    o = opts or {}
    bm, inc, issues_of = _rows_from(r, state, mask, o)
    top = bm["rows"][:max(0, int(_o(o, "backlog_issues", 15)))]
    out = []
    for i, c in enumerate(top):
        notes, bare, total, _ids = F._issue_notes(r, issues_of, c["name"], o)
        notes = notes[:int(_o(o, "act_notes", 40))]
        shorts, seen = [], set()
        for it in sorted([x for x in inc if issues_of.get(x.get("sys_id")) == c["name"]], key=lambda x: x.get("opened_at") or "", reverse=True):
            t = " ".join(str(it.get("short_description") or "").split())
            if t and t.lower() not in seen:
                seen.add(t.lower())
                shorts.append(F.apply_mask(t, mask)[:160])
            if len(shorts) >= 6:
                break
        x = {"id": "B%d" % (i + 1), "name": c["name"], "label": c["label"], "cluster": c["id"], "hash": "", "cat": c["cat"], "n": c["n"],
             "rate": round(c["rate"], 1), "hand": c["hand"], "best": c["best"], "defl": [p["defl"] for p in c["ph"]],
             "notes": [[e["count"], F.apply_mask(e["text"], mask)[:300]] for e in notes[:25]], "bare": bare, "total": total,
             "shorts": shorts}
        x["hash"] = backlog_hash(c["ids"], evidence=backlog_evidence({"issues": [x]}, mask, o))
        out.append({"items": [{"id": x["id"]}], "issues": [x]})
    return out


def backlog_evidence(batch, mask=None, opts=None):
    F = _F()
    lines = []
    red = lambda t: TICKET_NO.sub("[ticket]", F.apply_mask(t, mask))        # noqa: E731 - the mask, and a ticket number in free text too
    for x in batch.get("issues") or []:
        lines += ["CLUSTER %s: %s (category %s) - %d incidents, %.1f a month, %d handoffs between groups; the automation phases would settle about %d%%, %d%% and %d%% of them." % (
            x["id"], red(x.get("label") or x["name"]), red(x["cat"]) or "none", x["n"], x["rate"], x["hand"], x["defl"][0], x["defl"][1], x["defl"][2]),
            "EXAMPLE SHORT DESCRIPTIONS:"]
        lines += ["- %s" % red(t) for t in x["shorts"]] or ["(none)"]
        lines.append("CLOSE NOTES THAT SAY SOMETHING (times used | note):")
        lines += ["%d | %s" % (c, red(t)) for c, t in x["notes"]] or ["(none)"]
        lines.append("")
    return "\n".join(lines)


def backlog_task(opts=None):
    F = _F()
    o = opts or {}
    lim = LIMITS
    return "\n".join([
        "For the IT incident cluster in the evidence piped to you, write the text of an automation proposal. You cannot run any "
        "commands in this task, and none are needed. All numbers in the evidence are final: do not work out, repeat or change any; "
        "write about the cause and the fix, not about the figures.",
        "",
        "Give:",
        "- strategy: one short sentence, how this cluster should be automated (at most %d characters);" % lim["strategy"],
        "- ai_label: exactly one of \"Development-Assist\" (ACT helps people build the automation), \"Runtime-Integrated\" "
        "(ACT runs inside the automation when it works) or \"None\" (no AI needed);",
        "- summary: two sentences an executive reads, what repeats and what automating it would change (at most %d characters);" % lim["summary"],
        "- problem: two sentences, what goes wrong for the user and why it takes people's time (at most %d characters);" % lim["problem"],
        "- phases: exactly three, in order - 1 self-service and knowledge article, 2 a ServiceNow catalog item or flow that runs a script, "
        "3 zero-touch (the script runs on the event and the ticket closes itself). For each: approach (one sentence, at most %d "
        "characters), needs (2 to %d short items, each at most %d characters: what must exist first) and ai_note (one short "
        "sentence on what ACT does in that phase, or \"None\", at most %d characters)." % (lim["approach"], NEEDS_MAX, lim["need"], lim["ai_note"]),
        "Never the words purchase, buy or cost. Placeholders such as PERSON-3, HOST-12 stand for masked values: keep them out. "
        "No ticket numbers, hosts or people. No markdown.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (F.MARK_BEGIN, F.MARK_END),
        F.MARK_BEGIN,
        '{"items": [{"id": "B1", "strategy": "...", "ai_label": "Development-Assist", "summary": "...", "problem": "...", "phases": '
        '[{"approach": "...", "needs": ["...", "..."], "ai_note": "..."}, {"approach": "...", "needs": ["..."], "ai_note": "..."}, '
        '{"approach": "...", "needs": ["..."], "ai_note": "None"}]}]}',
        F.MARK_END,
    ] + (["", str(o["extra"])] if o.get("extra") else []))


_BANNED = re.compile(r"\b(purchas\w*|buy\w*|bought|costs?|costly)\b", re.I)


def _txt(s, limit):
    t = " ".join(str(s if s is not None else "").split())
    t = _BANNED.sub("", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


TICKET_NO = re.compile(r"\b[A-Za-z]{2,8}(?:\d{6,}|0\d{3,5})\b")              # INC0012345, xinc0000042, CHG123456: any case, zero-padded or 6+ digits


def _prose(t, mask, limit):
    """Words that may reach ACT or the report database: the people, hosts, addresses and numbers the run knows replaced
    (apply_mask), what the scanner still finds replaced by its kind ([person], [host] ...), a ticket number as [ticket]."""
    F = _F()
    t = F.PLACEHOLDER.sub(lambda m: "[%s]" % m.group(1).lower(), F.apply_mask(t, mask))
    found = {}
    F._scan(t, found, F.KEEP_WORDS)
    for real in sorted(found, key=len, reverse=True):
        t = t.replace(real, "[%s]" % found[real].lower())
    t = TICKET_NO.sub("[ticket]", t)
    t = " ".join(t.split())
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def clean_draft(d, mask=None):
    """A draft with every text put through _prose (the strategy, summary, problem, each phase's approach, needs and AI note)."""
    lim = LIMITS
    out = dict(d)
    for k in ("strategy", "summary", "problem"):
        out[k] = _prose(d[k], mask, lim[k])
    out["phases"] = [{"approach": _prose(p["approach"], mask, lim["approach"]), "needs": [x for x in (_prose(n, mask, lim["need"]) for n in p["needs"]) if x],
                      "ai_note": _prose(p["ai_note"], mask, lim["ai_note"])} for p in d["phases"]]
    return out


def read_draft(obj, mask=None):
    """One ACT item (a dict) -> the draft the page keeps, or None when it is not usable (no strategy, summary or problem, or not three
    phases). Every text is cut to its limit; the label is one of the three."""
    if not isinstance(obj, dict):
        return None
    lim = LIMITS
    d = {"strategy": _txt(obj.get("strategy"), lim["strategy"]), "summary": _txt(obj.get("summary"), lim["summary"]),
         "problem": _txt(obj.get("problem"), lim["problem"])}
    lab = " ".join(str(obj.get("ai_label") or "").split()).lower()
    d["ai_label"] = {"development-assist": AI_LABELS[0], "runtime-integrated": AI_LABELS[1], "none": AI_LABELS[2]}.get(lab, "")
    phs = obj.get("phases")
    if not d["strategy"] or not d["summary"] or not d["problem"] or not d["ai_label"] or not isinstance(phs, list) or len(phs) < 3:
        return None
    d["phases"] = []
    for p in phs[:3]:
        if not isinstance(p, dict) or not _txt(p.get("approach"), 5):
            return None
        needs = p.get("needs") if isinstance(p.get("needs"), list) else [x for x in re.split(r"[;,]", str(p.get("needs") or "")) if x.strip()]
        needs = [t for t in (_txt(x, lim["need"]) for x in needs) if t][:NEEDS_MAX]
        ai_note = _txt(p.get("ai_note"), lim["ai_note"])
        if not needs or not ai_note:                       # a phase with no needs or no AI note is incomplete: the answer is not used
            return None
        d["phases"].append({"approach": _txt(p.get("approach"), lim["approach"]), "needs": needs, "ai_note": ai_note})
    return d


def backlog_merge(state, raw, batch, opts=None, ran=True, reason="", model="", mask=None):
    """ACT's answer for one cluster into state["backlog"][name] = {draft..., hash, from: "act"}; bad JSON or an unusable draft:
    nothing is kept (the cluster reads "not drafted") and the job says so."""
    F = _F()
    st = json.loads(json.dumps(state or F.servicenow_insights_state()))
    st.setdefault("backlog", {})
    st["asked"] = int(st.get("asked") or 0) + 1
    if model and not st.get("model"):
        st["model"] = model
    names = dict((x["id"], x) for x in batch.get("issues") or [])
    note = F._answer_note(raw, ran, reason)
    label = ", ".join(x["name"] for x in names.values())
    if note:
        st["errors"].append("backlog text for %s: %s" % (label, note))
        return st
    obj = F._parse_json(raw)
    items = obj.get("items") if isinstance(obj, dict) else None
    got = False
    for it in items if isinstance(items, list) else []:
        x = names.get(str(it.get("id") or "")) if isinstance(it, dict) else None
        d = read_draft(it) if x else None
        if d:
            d = clean_draft(d, mask)
            d["hash"] = x["hash"]
            st["backlog"][x["name"]] = d
            got = True
    if got:
        st["answered"] = int(st.get("answered") or 0) + 1
    else:
        st["errors"].append("backlog text for %s: the answer could not be read (not JSON, or a field missing)" % label)
    return st


def backlog_apply_cache(state, batches, held):
    """The drafts the report database holds ({hash: JSON text}) placed in the state; the batches left to ask (a cluster whose
    hash is held is not asked): {state, batches, hits}."""
    F = _F()
    st = json.loads(json.dumps(state or F.servicenow_insights_state()))
    st.setdefault("backlog", {})
    held = held or {}
    left, hits = [], 0
    for b in batches or []:
        x = (b.get("issues") or [{}])[0]
        d = None
        if x.get("hash") in held:
            try:
                d = read_draft(json.loads(held[x["hash"]]))
            except (ValueError, TypeError):
                d = None
        if d:
            d["hash"] = x["hash"]
            d["cached"] = True
            st["backlog"][x["name"]] = d
            hits += 1
        else:
            left.append(b)
    return {"state": st, "batches": left, "hits": hits}


def backlog_cache_rows(state):
    """What goes to the report database: [{hash, text}] of every draft ACT wrote or the cache gave (a draft is ACT's
    masked prose - no ticket number, host or person)."""
    out = []
    for name, d in sorted(((state or {}).get("backlog") or {}).items()):
        if not isinstance(d, dict) or not d.get("hash"):
            continue
        keep = clean_draft(dict((k, d[k]) for k in ("strategy", "summary", "problem", "ai_label", "phases") if k in d))
        out.append({"hash": d["hash"], "text": json.dumps(keep, sort_keys=True)})
    return out


# ---- the model's part: the clusters with their words ------------------------------------------------------------

def backlog_model(tickets, issues_of, since, until, state, opts=None, vip=None, repeat_days=7.0):
    """backlog_rows with ACT's words from the state put in: each cluster gets text ({strategy, summary, problem, ai_label,
    phases} or None) and why it has none ("ACT off", "outside the top N", "no usable answer")."""
    o = opts or {}
    bm = backlog_rows(tickets, issues_of, since, until, o, vip, repeat_days)
    drafts = (state or {}).get("backlog") or {}
    act_on = bool(o.get("act")) or int((state or {}).get("answered") or 0) > 0
    top_n = max(0, int(_o(o, "backlog_issues", 15)))
    asked = bool(act_on and int((state or {}).get("asked") or 0) > 0 or drafts)
    for i, c in enumerate(bm["rows"]):
        d = drafts.get(c["name"])
        c["text"] = d if isinstance(d, dict) and d.get("phases") else None
        if c["text"]:
            c["why_not"] = ""
        elif not act_on:
            c["why_not"] = OFF_TEXT
        elif i >= top_n:
            c["why_not"] = "not drafted (outside the top %d)" % top_n
        elif not asked:
            c["why_not"] = "not drafted (ACT was not asked in this run)"
        else:
            c["why_not"] = "not drafted (ACT gave no usable answer)"
    bm["act_on"] = act_on
    bm["top_n"] = top_n
    return bm


# ---- the proposal as text (the .md the page builds in the browser has the same layout) ---------------------------

def proposal_md(c, bm):
    t = c.get("text") or {}
    miss = c.get("why_not") or OFF_TEXT
    L = ["# %s" % c["label"], "", "Category: %s · AI: %s" % (c["cat"] or "none", t.get("ai_label") or "not drafted"), "",
         "| Tickets | Per month | Hours saved / yr | VIP | Score |", "|---:|---:|---:|---:|---:|",
         "| %s | %.1f | %s | %s | %d |" % (_fmt(c["n"]), c["rate"], _fmt(c["saved"]), _fmt(c["vip"]) if bm.get("vip_on") else "n/a", c["score"]), "",
         "## AI strategy", "", t.get("strategy") or miss, "", "## Executive summary", "", t.get("summary") or miss, "", "## Problem", "", t.get("problem") or miss, ""]
    for i, p in enumerate(c["ph"]):
        d = (t.get("phases") or [None, None, None])[i] or {}
        L += ["## Phase %d: %s%s" % (i + 1, p["name"], " (best)" if c["best"] == i + 1 else ""), "",
              "- Complexity %d/5 · impact %d/5 · effort %d day%s · deflection %d%% · score %d" % (
                  p["complexity"], p["impact"], p["effort"], "" if p["effort"] == 1 else "s", p["defl"], p["score"]),
              "- Approach: %s" % (d.get("approach") or miss), "- Needs: %s" % (", ".join(d.get("needs") or []) or miss), "- AI: %s" % (d.get("ai_note") or miss), ""]
    return "\n".join(L)


def backlog_docx(model, opts=None, key="all"):
    """The combined Automation backlog.docx as site_docx's description: the top backlog_issues clusters (the ones ACT wrote for)
    - a table of every cluster in the first section, then each proposal. {} when there is nothing to write."""
    o = opts or {}
    s = ((model or {}).get("scopes") or {}).get(key or "all") or {}
    bm = s.get("autobl") or {}
    rows = bm.get("rows") or []
    if not rows:
        return {}
    n = max(0, int(_o(o, "backlog_issues", 15)))
    blocks = [{"type": "title", "text": "Automation backlog"},
              {"type": "para", "text": "%s clusters of incidents, %s incidents in the window (%.1f months). Priority P1 from score %d, P2 from %d." % (
                  _fmt(len(rows)), _fmt(bm["total"]), bm["months"], CUT_P1, CUT_P2), "keep": True},
              {"type": "table", "columns": ["Priority", "Issue", "Tickets / month", "Hours saved / yr", "VIP", "Score"],
               "widths": [8, 44, 12, 14, 8, 8],
               "rows": [["P%d" % r["prio"], r["label"], "%.1f" % r["rate"], _fmt(r["saved"]), _fmt(r["vip"]) if bm.get("vip_on") else "n/a", str(r["score"])]
                        for r in rows]}]
    for c in rows[:n]:
        t = c.get("text") or {}
        miss = c.get("why_not") or OFF_TEXT
        blocks += [{"type": "heading", "text": c["label"]},
                   {"type": "para", "text": "Category %s · AI %s · %s tickets, %.1f a month · %s hours saved a year · VIP %s · score %d (P%d)" % (
                       c["cat"] or "none", t.get("ai_label") or "not drafted", _fmt(c["n"]), c["rate"], _fmt(c["saved"]),
                       _fmt(c["vip"]) if bm.get("vip_on") else "n/a", c["score"], c["prio"])},
                   {"type": "field", "label": "AI strategy", "text": t.get("strategy") or miss},
                   {"type": "field", "label": "Executive summary", "text": t.get("summary") or miss},
                   {"type": "field", "label": "Problem", "text": t.get("problem") or miss}]
        for i, p in enumerate(c["ph"]):
            d = (t.get("phases") or [None, None, None])[i] or {}
            blocks.append({"type": "labelled", "items": [
                {"label": "Phase %d: %s%s" % (i + 1, p["name"], " (best)" if c["best"] == i + 1 else ""),
                 "text": "complexity %d/5, impact %d/5, effort %d day%s, deflection %d%%, score %d" % (
                     p["complexity"], p["impact"], p["effort"], "" if p["effort"] == 1 else "s", p["defl"], p["score"])},
                {"label": "Approach", "text": d.get("approach") or miss}, {"label": "Needs", "text": ", ".join(d.get("needs") or []) or miss},
                {"label": "AI", "text": d.get("ai_note") or miss}]})
    return {"banner": str(o.get("banner") or ""), "banner_color": str(o.get("banner_color") or ""), "logo": str(o.get("logo") or ""),
            "logo_height": int(o.get("logo_height") or 64), "blocks": blocks}


def backlog_logo(f, playbook_dir=""):
    """The logo file as the incident documents find it: a full path as it is, else under the playbook directory (a leading playbooks/ dropped)."""
    f = str(f or "").strip()
    if not f:
        return ""
    return f if f.startswith("/") else "%s/%s" % (str(playbook_dir).rstrip("/"), f[10:] if f.startswith("playbooks/") else f)


def backlog_sheet(bm):
    """The workbook sheet (site_email's xlsx: {name, columns, rows}): every cluster and each phase of it."""
    rows = []
    for r in (bm or {}).get("rows") or []:
        t = r.get("text") or {}
        line = ["P%d" % r["prio"], r["label"], r["cat"], t.get("ai_label") or "", r["n"], round(r["rate"], 1), round(r["share"], 1), round(r["saved"]),
                r["vip"] if bm.get("vip_on") else "", r["score"], r["best"], r["hand"]]
        for i, p in enumerate(r["ph"]):
            d = (t.get("phases") or [None, None, None])[i] or {}
            line += [p["complexity"], p["impact"], p["effort"], p["defl"], p["score"], d.get("approach") or "", ", ".join(d.get("needs") or []), d.get("ai_note") or ""]
        line += [t.get("strategy") or r.get("why_not") or "", t.get("summary") or "", t.get("problem") or ""]
        rows.append(line)
    cols = ["Priority", "Issue", "Category", "AI", "Tickets", "Per month", "Share %", "Hours saved / yr", "VIP", "Score", "Best phase", "Handoffs"]
    for i in (1, 2, 3):
        cols += ["P%d complexity" % i, "P%d impact" % i, "P%d effort (days)" % i, "P%d deflection %%" % i, "P%d score" % i,
                 "P%d approach" % i, "P%d needs" % i, "P%d AI" % i]
    cols += ["AI strategy", "Executive summary", "Problem"]
    return {"name": "Automation backlog", "columns": cols, "rows": rows}


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_backlog_batches": backlog_batches, "servicenow_insights_backlog_evidence": backlog_evidence,
                "servicenow_insights_backlog_task": backlog_task, "servicenow_insights_backlog_merge": backlog_merge,
                "servicenow_insights_backlog_apply_cache": backlog_apply_cache, "servicenow_insights_backlog_cache_rows": backlog_cache_rows,
                "servicenow_insights_backlog_docx": backlog_docx, "servicenow_insights_backlog_hash": backlog_hash, "servicenow_insights_backlog_logo": backlog_logo}
