# -*- coding: utf-8 -*-
"""1.104.0: ACT on the outage review (vm_outage_act). ACT reads the review's time line - the lead-up first, then the
first critical event and the events around it - and answers with possible causes, each with read-only checks and a
possible fix (plugins/module_utils/act_causes.py's shape, a step's kind: check or fix). It runs no command: the job
hands it the events as evidence (roles/site_act evidence mode). Its answer becomes cards that fold, marked as
suggestions, at the bottom of the review page (report_cause_cards, report_causes.html.j2 act_suggest); the time line's
ACT lane keeps only what ACT wrote in tickets. docs/VM_OUTAGE_WATCH.md, "ACT's suggestions".

    outage_act_input(timeline, opts)          -> {items, evidence, task, ids, names, count, left_out}
    outage_act_section(timeline, parsed, opts) -> {web: the section or None, email: [sections], judged: a line or ''}
    outage_timeline_doc (outage_timeline_filters.py) puts them in the report.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import importlib.util
import os
import re

_here = os.path.dirname(os.path.abspath(__file__))
_sp = importlib.util.spec_from_file_location("act_causes_for_outage", os.path.join(os.path.dirname(_here), "module_utils", "act_causes.py"))
act_causes = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(act_causes)
_sp = importlib.util.spec_from_file_location("site_filters_for_outage", os.path.join(_here, "site_filters.py"))
_sf = importlib.util.module_from_spec(_sp)
_sp.loader.exec_module(_sf)

MARK_BEGIN, MARK_END = act_causes.MARK_BEGIN, act_causes.MARK_END
MAX_EVENTS = 120                      # vm_outage_act_max_events: the events ACT reads at most (the lead-up, then the nearest)
LEVEL = {"critical": "critical", "warning": "warning", "low": "low", "info": "info", "ok": "info"}
WORD = {"critical": "Critical", "warning": "Warning", "low": "Low", "info": "Note", "ok": "Back to normal"}
NOTE = ("ACT read this time line (the lead-up first, then the first critical event and the events around it) and wrote these. "
        "They are suggestions: ACT ran nothing on a host or on vCenter. Check each one before you act.")
TITLE = "From ACT - possible causes and fixes"
REF = re.compile(r"(?<![\w&])#(\d+)\b")


def _line(e):
    """One event as ACT reads it: its number, time, level, source, where and who, and what happened."""
    who = []
    for k, w in (("h", "host"), ("vm", "VM"), ("cl", "cluster"), ("v", "vCenter")):
        if e.get(k) and e.get(k) != e.get("p"):
            who.append("%s %s" % (w, e[k]))
    if e.get("vms"):
        who.append("also VMs %s" % ", ".join(e["vms"][:12]) + (" and %d more" % (len(e["vms"]) - 12) if len(e["vms"]) > 12 else ""))
    parts = ["#%d" % e["n"], e.get("at") or e.get("t") or "", WORD.get(e.get("l"), e.get("l") or ""), e.get("lane") or "",
             e.get("p") or "-", "; ".join(who) or "-", e.get("w") or ""]
    if e.get("msg") and e["msg"] not in (e.get("w") or ""):
        parts.append(e["msg"])
    return " | ".join(str(x) for x in parts)


def _pick(evs, first, cap):
    """-> (lead-up, the first critical event or None, the events around it in time order, how many were left out). The first
    critical event and the lead-up's own problems (lead) first - within cap, the latest of the lead-up when it is longer
    (Neon) -; then the events nearest to it in time, up to cap."""
    crit = next((e for e in evs if e["n"] == first), None)
    lead = [e for e in evs if e.get("lead") and e is not crit]
    keep = max(0, cap - (1 if crit else 0))
    lead = lead[len(lead) - keep:] if len(lead) > keep else lead
    rest = [e for e in evs if e not in lead and e is not crit]
    room = max(0, cap - len(lead) - (1 if crit else 0))
    if crit is not None:
        rest.sort(key=lambda e: (abs(e["s"] - crit["s"]), e["n"]))
    near = sorted(rest[:room], key=lambda e: e["n"])
    return lead, crit, near, len(rest) - len(near)


def outage_act_input(timeline, opts=None):
    """The time line -> what ACT is asked: items (one per event it reads, id "#n", in report_cause_cards' shape), the
    evidence (its lines, the lead-up first), the task (the prompt), the ids, the names ACT masks, how many it reads and
    how many were left out. opts: max_events, extra (site_act_extra_instructions)."""
    o = opts or {}
    a = (timeline or {}).get("act_in") or {}
    evs = list(a.get("events") or [])
    cap = int(o.get("max_events") or MAX_EVENTS)
    lead, crit, near, left = _pick(evs, a.get("first") or 0, max(1, cap))
    shown = lead + ([crit] if crit else []) + near
    head = ["THE OUTAGE REVIEW'S TIME LINE, %s%s. %d event(s) in the window; %d below%s." % (
        a.get("window") or "", ("; the outage is about " + a["subject"]) if a.get("subject") else "", len(evs), len(shown),
        (", %d left out (the furthest in time from the first critical event)" % left) if left else ""),
        "Each line: #number | time | level | source | where | who (host, VM, cluster, vCenter) | what happened | the message.", ""]
    body = []
    if lead:
        body += ["THE LEAD-UP (the problems about the outage's subject before its first critical event):"] + [_line(e) for e in lead] + [""]
    if crit:
        body += ["THE FIRST CRITICAL EVENT:", _line(crit), ""]
        body += ["THE EVENTS AROUND IT, in order:" if near else "No other event in the window."] + [_line(e) for e in near]
    else:
        body += ["No critical event in the window. THE EVENTS, in order:"] + [_line(e) for e in near]
    items = []
    for e in shown:
        items.append({"id": "#%d" % e["n"], "level": LEVEL.get(e.get("l"), "info"), "title": e.get("w") or "", "count": 1,
                      "hosts": [x for x in [e.get("h") or e.get("p")] if x],
                      "examples": [{"time": e.get("t") or "", "host": e.get("p") or "", "message": e.get("w") or "", "n": e["n"],
                                    "sort": "%06d" % (999999 - e["n"])}]})
    names = _mask_names(x for e in evs for x in [e.get("h"), e.get("vm"), e.get("cl"), e.get("v"), e.get("p")] + list(e.get("vms") or []))
    task = [
        "VMware outage review for an operations team: what most likely caused the outage, what to check to be sure, and "
        "what could fix it.",
        "You cannot run any commands in this task, and none are needed: the evidence piped to you is the review's time line, "
        "read from vCenter and ServiceNow. Each event has a number (#26). Base every conclusion on those events and name "
        "them by their number. Read the lead-up first, then the first critical event and the events around it.",
        "",
        "Group the events that share a cause into ONE cause. For each cause give:",
        "- title: the cause in one line;",
        "- why: one to three sentences that name the events (#26) showing it;",
        "- patterns: the numbers of the events it explains, written \"#26\" - each event in one cause only;",
        "- confidence: a whole number 0-100 (80+ = the events show it, 50-79 = likely, under 50 = a guess);",
        "- steps, in order: first the checks that confirm the cause without changing anything (kind \"check\": what to "
        "look at in the vSphere Client, an esxcli or PowerCLI Get- command, the ServiceNow ticket), then the possible "
        "fix (kind \"fix\"). A person runs them: never claim you ran or changed anything. Name only events from the evidence.",
        "Events that are harmless here (a usage alarm back within a minute, a routine note) go in noise. An event in no "
        "cause and not in noise is simply not shown.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"overall": "what to check first, in one or two sentences",',
        ' "causes": [{"key": "short-name", "title": "the cause in one line", "why": "why, naming the events (#26, #27)",',
        '             "patterns": ["#26", "#27"], "hosts": ["esx07"], "confidence": 80,',
        '             "steps": [{"kind": "check", "do": "what to look at", "cmd": "the exact command, or empty", "why": "one sentence"},',
        '                       {"kind": "fix", "do": "what could fix it", "cmd": "", "why": ""}]}],',
        ' "noise": ["#1"], "noise_why": {"#1": "why it is harmless"}}',
        MARK_END,
        "key: a-z, 0-9 and -. At most %d steps per cause. Plain text inside the JSON strings (no markdown)." % act_causes.MAX_STEPS,
        "Write # only before an event's number (#26). Write any other number without it (PSU 2, vmnic1, slot 3).",
    ]
    if o.get("extra"):
        task += ["", str(o["extra"])]
    return {"items": items, "evidence": "\n".join(head + body), "task": "\n".join(task), "ids": [i["id"] for i in items],
            "names": names, "count": len(shown), "left_out": left}


def _mask_names(xs):
    """The names ACT masks (ACT_PSEUDO_NAMES): one word each - ACT splits a name at its spaces, so "web server 01" would mask
    "web" and "server" everywhere (Neon). A name with spaces gives its words that are names (a digit, -, _ or . in them);
    an address is never one (ACT masks addresses by itself)."""
    out = set()
    for x in xs:
        x = str(x or "").strip()
        for w in ([x] if x and not re.search(r"\s", x) else [w for w in x.split() if len(w) >= 3 and re.search(r"[\d._-]", w)]):
            if not re.match(r"^[\d.:]+$", w):
                out.add(w)
    return sorted(out)


def _ranges(ids):
    """['#1', '#2', '#3', '#7'] -> '#1 to #3, #7'."""
    ns = sorted(set(int(x.lstrip("#")) for x in ids if re.match(r"^#\d+$", str(x))))
    out, i = [], 0
    while i < len(ns):
        j = i
        while j + 1 < len(ns) and ns[j + 1] == ns[j] + 1:
            j += 1
        out.append("#%d" % ns[i] if i == j else "#%d to #%d" % (ns[i], ns[j]) if j > i + 1 else "#%d, #%d" % (ns[i], ns[j]))
        i = j + 1
    return ", ".join(out)


def _clean(parsed, ids, top=0):
    """ACT's causes held to the page: patterns only of events it was given; a step that names an event it did not read
    (in what to do, why or the command) is dropped (so is a cause left with none of its events); in a cause's title and
    why, and in the overall line, such a number is said to be one ACT did not read. A #number past the page's last event
    (top) is an event only when the text says so ("event #99"); else it is something else's (PSU #40): left as it is.
    -> (causes, noise, dropped steps, overall)."""
    known = set(ids)
    top = int(top or 0) or max([int(i.lstrip("#")) for i in known] or [0])

    def unknown(*texts):
        out = set()
        for t in texts:
            t = str(t or "")
            for m in REF.finditer(t):
                n = int(m.group(1))
                if "#%d" % n not in known and (n <= top or "event" in t[max(0, m.start() - 24):m.start()].lower()):
                    out.add(n)
        return sorted(out)

    def said(text):
        bad = set(unknown(text))
        return REF.sub(lambda m: m.group(0) + (" (an event ACT did not read)" if int(m.group(1)) in bad else ""), str(text)) if bad else text
    causes, dropped = [], 0
    for c in (parsed or {}).get("causes") or []:
        pats = [p for p in c.get("patterns") or [] if p in known]
        if not pats:
            continue
        steps = []
        for st in c.get("steps") or []:
            if unknown(st.get("do"), st.get("why"), st.get("cmd")):
                dropped += 1
                continue
            steps.append(st)
        c = dict(c, patterns=pats, steps=steps, title=said(c.get("title") or ""))
        if c.get("why"):
            c["why"] = said(c["why"])
        causes.append(c)
    named = set(p for c in causes for p in c["patterns"])
    noise = [p for p in (parsed or {}).get("noise") or [] if p in known and p not in named]
    overall = said((parsed or {}).get("overall") or "")
    return causes, noise, dropped, overall


def outage_act_section(timeline, parsed, opts=None):
    """ACT's answer (act_batches_merge / act_analysis_parse with causes) -> what the report shows. opts: ran, reason,
    asked (ACT was asked: vm_outage_act on), model, web (a report website: report_web_dest), max_events, input
    (outage_act_input's result, when it was made). -> {web: the cards' section (act_suggest) or None, email: [a section],
    judged: one line for How it judged when there is nothing to show ('' when ACT was not asked)}."""
    o = opts or {}
    if not o.get("asked"):
        return {"web": None, "email": [], "judged": ""}
    inp = o.get("input") or outage_act_input(timeline, {"max_events": o.get("max_events")})
    pa = parsed or {}
    why = ""
    if not o.get("ran"):
        why = str(o.get("reason") or "ACT gave no answer.").strip()
    elif not pa.get("parsed") or "causes" not in pa:
        why = "its answer could not be read (%s)." % (pa.get("error") or "no causes in its JSON")
    top = max([int(e.get("n") or 0) for e in ((timeline or {}).get("act_in") or {}).get("events") or []] or [0])
    causes, noise, dropped, overall = _clean(pa, inp["ids"], top) if not why else ([], [], 0, "")
    if not why and not causes:
        why = "it named no cause for the events it read."
    if why:
        line = "ACT (vm_outage_act): %s No suggestions this time." % (why if why.endswith(".") else why + ".")
        return {"web": None, "email": [], "judged": line}
    named = set(p for c in causes for p in c["patterns"])
    items = [i for i in inp["items"] if i["id"] in named]
    cc = _sf.report_cause_cards({"causes": causes, "noise": []}, items, {"noun": "event", "max_evidence": 6})
    sec = dict(cc["web"][-1])
    n = len(sec.get("causes") or [])
    extra = []
    if noise:
        extra.append("Left out as harmless: %s." % _ranges(noise))
    if inp.get("left_out"):
        extra.append("ACT read %d of the events (the lead-up and the nearest to the first critical event; vm_outage_act_max_events)."
                     % inp["count"])
    if dropped:
        extra.append("%d step%s that named events not on this page left out." % (dropped, "" if dropped == 1 else "s"))
    count = "%d possible cause%s from %d event%s%s." % (n, "" if n == 1 else "s", len(named), "" if len(named) == 1 else "s",
                                                     (" (" + o["model"] + ")") if o.get("model") else "")
    sec.update(title=TITLE, act_suggest=True, status="info", noise=[], act_note=NOTE, count_text=" ".join([count] + extra))
    if overall:
        sec["cause_overall"] = [str(overall)]
    # 1.104.0 (Neon P1): the email always says the causes in plain form, with their first check - a report website may
    # show this email's doc (a theme without cards), may not have been published, or report_web_dest may be blank; with a
    # website that shows the cards (web: report_web_dest and an atlas / brief / ops theme) one more line points there
    lines = [NOTE]
    if o.get("web"):
        lines.append("The cards on this report's web page have every check and the possible fix of each.")
    for k in sec["causes"]:
        chk = next((st for st in k["steps"] if st.get("kind") == "check"), None)
        lines.append("Cause %d (%s%s, events %s): %s%s" % (
            k["n"], k["level_word"], (", " + k["conf_text"]) if k.get("conf_text") else "", ", ".join(k["patterns"]), k["title"],
            (" First check: %s" % chk["do"]) if chk and chk.get("do") else ""))
    email = [{"title": TITLE, "status": "info", "lines": lines}]
    return {"web": sec, "email": email, "judged": ""}


# 1.104.0 (Codex): what these filters return quotes vCenter events and ServiceNow tickets. On ansible-core 2.16 a filter's
# result that holds "{{ ... }}" is templated again where it is used - a ticket could run a lookup on the controller (and
# show a credential to ACT or on the page). The results are marked unsafe (never templated); 2.19+ never templates them.
try:
    from ansible.utils.unsafe_proxy import wrap_var as _wrap
except ImportError:                     # the tests' own imports without ansible
    def _wrap(v):
        return v


def _untrusted(f):
    def g(*a, **k):
        return _wrap(f(*a, **k))
    g.__name__, g.__doc__ = f.__name__, f.__doc__
    return g


class FilterModule(object):
    def filters(self):
        return {"outage_act_input": _untrusted(outage_act_input), "outage_act_section": _untrusted(outage_act_section)}
