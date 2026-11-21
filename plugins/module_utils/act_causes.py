# -*- coding: utf-8 -*-
"""ACT's answer as causes - one shared shape for every report that asks ACT why (log analysis first; the alarms, health
check, fapolicyd and right-sizing analyses after it) and for the cause cards that show it (plugins/filter/site_filters.py,
report_cards_*). docs/ACT.md, "Causes".

    {"overall": "...", "causes": [{"key": "pulseaudio-selinux", "title": "...", "patterns": ["P1", "P2"], "hosts": ["app01"],
      "confidence": 85, "evidence": ["a quoted line"], "steps": [{"do": "...", "cmd": "...", "why": "..."}]}],
     "noise": ["P7"], "noise_why": {"P7": "..."}}

parse(text, ids) reads an answer (the JSON between MARK_BEGIN and MARK_END, or the answer's one JSON object) into that
shape, holding it to SCHEMA: what does not fit is cut, dropped or mended, and said in errors (short strings). An answer of
the older shape - one item per pattern, {"items": [{"id", "cause", "evidence", "fix", "confidence"}]} - becomes one cause
per item. merge(parsed answers) joins the answers of parallel batches; its result does not depend on their order.
Python 3.6 and the standard library only (it may run on a host).
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import re

PROMPT_VERSION = "causes-1"          # what an answer cache keys on: a new prompt shape, a new version
MARK_BEGIN = "BEGIN_ACT_ANALYSIS"     # the markers every ACT analysis of the repository answers between
MARK_END = "END_ACT_ANALYSIS"
MAX_CAUSES = 40
MAX_TITLE = 120
MAX_KEY = 40
MAX_EVIDENCE = 5
MAX_LINE = 300
MAX_STEPS = 6
MAX_STEP = 600                       # a step's do / why (a cmd may be longer: a here-doc)
MAX_CMD = 4000
WORD_CONFIDENCE = {"high": 85, "medium": 65, "low": 35}

SCHEMA = {
    "overall": "two or three sentences: what to do first, and which items share a cause (optional)",
    "causes": [{
        "key": "a short stable name for the cause: a-z, 0-9 and -, at most %d characters, unique in the answer" % MAX_KEY,
        "title": "the cause in one line, at most %d characters" % MAX_TITLE,
        "patterns": "the ids of the items it explains - each item in one cause only",
        "hosts": "the hosts it is seen on (optional)",
        "confidence": "how sure, a whole number 0-100: 80+ the evidence shows it, 50-79 likely, under 50 a guess",
        "evidence": "at most %d short lines quoted from the evidence, each at most %d characters" % (MAX_EVIDENCE, MAX_LINE),
        "steps": "at most %d, in order: {do: the action in plain words, cmd: the exact command or '', why: one sentence or ''}"
                 % MAX_STEPS}],
    "noise": "the ids of the items that are harmless noise (in no cause)",
    "noise_why": "{id: one sentence} for the noise (optional)",
}


def band(n):
    """A confidence -> high (80+), medium (50-79), low (under 50); '' when there is none."""
    if n is None:
        return ""
    return "high" if n >= 80 else "medium" if n >= 50 else "low"


def prompt_lines(item_word="item"):
    """The lines a prompt ends with: the JSON shape ACT answers in, between the markers."""
    return [
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"overall": "what to do first, in two or three sentences",',
        ' "causes": [{"key": "short-name", "title": "the cause in one line", "patterns": ["P1", "P2"], "hosts": ["host1"],',
        '             "confidence": 85, "evidence": ["a line quoted from the evidence"],',
        '             "steps": [{"do": "what to do", "cmd": "the exact command, or empty", "why": "one sentence"}]}],',
        ' "noise": ["P7"], "noise_why": {"P7": "why it is harmless"}}',
        MARK_END,
        "Group the %ss that share a cause into ONE cause; put every %s id in exactly one cause or in noise. key: a-z, 0-9 and -. "
        "confidence: a whole number 0-100 (80+ = the evidence shows it, 50-79 = likely, under 50 = a guess). At most %d steps, "
        "in the order to do them; cmd only when there is an exact command (a person runs it - never claim you ran anything). "
        "Plain text inside the JSON strings (no markdown)." % (item_word, item_word, MAX_STEPS),
    ]


def _text(v, cap):
    s = " ".join(str(v).split()) if v is not None and not isinstance(v, (dict, list)) else ""
    return s if len(s) <= cap else s[:cap - 3].rstrip() + "..."


def _cmd(v):
    """-> (the command, or '' when there is none or it is too long: a cut command is never offered to copy)."""
    if v is None or isinstance(v, (dict, list)):
        return "", False
    s = str(v).strip("\n").rstrip()
    return (s, False) if len(s) <= MAX_CMD else ("", True)


def _key(v):
    k = re.sub(r"[^a-z0-9-]+", "-", str(v or "").lower()).strip("-")
    return re.sub(r"-{2,}", "-", k)[:MAX_KEY].strip("-")


def _conf(v):
    """-> (0-100 or None, error or '')."""
    if isinstance(v, bool) or v is None or v == "":
        return None, "no confidence" if v in (None, "") else "confidence is not a number"
    if isinstance(v, str) and v.strip().lower() in WORD_CONFIDENCE:
        return WORD_CONFIDENCE[v.strip().lower()], ""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(v))      # "85", "85%", "about 70%" - the sign kept (Codex: -90 is no 90)
    if not m:
        return None, "confidence is not a number"
    n = float(m.group(0))
    if 0 < n <= 1 and (isinstance(v, float) or "." in m.group(0)):
        n *= 100                                        # a fraction (0.85 = 85%), as act_analysis_parse reads it; 1 = 1%
    if n < 0 or n > 100:
        return None, "confidence %s is not 0-100" % m.group(0)
    return int(round(n)), ""


def _strs(v):
    if v is None:
        return []
    if isinstance(v, (str, int)) and not isinstance(v, bool):
        return [str(v)]
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, (str, int)) and not isinstance(x, bool) and str(x).strip()]
    return []


def natural(s):
    """A sort key that puts P2 before P10 - and P1 before P01 (the text itself breaks a tie: never the input's order)."""
    return ([int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))], str(s))


def _object(text):
    """The answer's JSON object -> (dict or None, error or ''). Looked for between the markers, then in a ```json block,
    then as the answer's outermost {...}; a trailing comma is forgiven; a bare list is the older shape's items."""
    if isinstance(text, dict):
        return text, ""
    t = str(text or "")
    cands = []
    m = re.search(re.escape(MARK_BEGIN) + r"\s*(.*?)\s*" + re.escape(MARK_END), t, re.S)
    if m:
        cands.append(m.group(1))
    cands += re.findall(r"```(?:json)?\s*(.*?)```", t, re.S)
    if t.strip()[:1] in ("{", "["):
        cands.append(t.strip())                         # the whole answer (a bare list of the older shape too)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    if not cands:
        return None, "no JSON object in the answer"
    last = ""
    for c in cands:
        c = re.sub(r"^```(?:json)?\s*|\s*```$", "", c.strip())
        for attempt in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                v = json.loads(attempt)
            except ValueError as e:
                last = str(e)[:120]
                continue
            if isinstance(v, list):
                v = {"items": v}
            if isinstance(v, dict):
                return v, ""
    return None, "the JSON does not read: %s" % last


def _steps(v, where, errors):
    out = []
    for s in (v if isinstance(v, list) else [])[:MAX_STEPS * 3]:
        if isinstance(s, str):
            s = {"do": s}
        if not isinstance(s, dict):
            continue
        cmd, long_ = _cmd(s.get("cmd"))
        if long_:
            errors.append("%s: a command over %d characters left out" % (where, MAX_CMD))
        st = {"do": _text(s.get("do"), MAX_STEP), "cmd": cmd, "why": _text(s.get("why"), MAX_STEP)}
        if st["do"] or st["cmd"]:
            out.append(st)
    if len(out) > MAX_STEPS:
        errors.append("%s: %d steps, the first %d kept" % (where, len(out), MAX_STEPS))
        out = out[:MAX_STEPS]
    return out


def _cause(c, n, errors):
    """One cause as the answer gives it -> the shape of SCHEMA (or None when it is no cause at all)."""
    if not isinstance(c, dict):
        errors.append("cause %d is not an object" % n)
        return None
    title = _text(c.get("title"), MAX_TITLE)
    # no key and no title: a key of its patterns, never of its place in the answer - "cause-1" of two batches would
    # be joined by merge as one cause
    key = _key(c.get("key")) or _key(title) or _key("cause-" + "-".join(_strs(c.get("patterns")))) or "cause-%d" % n
    where = "cause %s" % key
    conf, why = _conf(c.get("confidence"))
    if why:
        errors.append("%s: %s" % (where, why))
    steps = _steps(c.get("steps"), where, errors)
    if not title:
        title = (steps[0]["do"] if steps and steps[0]["do"] else "Cause %d" % n)[:MAX_TITLE]
        errors.append("%s: no title" % where)
    ev = []
    for line in _strs(c.get("evidence"))[:MAX_EVIDENCE]:
        t = _text(line, MAX_LINE)
        if t:
            ev.append(t)
    if len(_strs(c.get("evidence"))) > MAX_EVIDENCE:
        errors.append("%s: more than %d evidence lines, the first %d kept" % (where, MAX_EVIDENCE, MAX_EVIDENCE))
    return {"key": key, "title": title, "patterns": [p.strip() for p in _strs(c.get("patterns"))], "hosts": _strs(c.get("hosts")),
            "confidence": conf, "evidence": ev, "steps": steps}


def _from_items(items, errors):
    """The older shape: one item per pattern -> one cause each."""
    out = []
    for n, it in enumerate(items if isinstance(items, list) else [], 1):
        if not isinstance(it, dict) or not str(it.get("id") or "").strip():
            errors.append("item %d has no id" % n)
            continue
        pid = str(it["id"]).strip()
        fix = _text(it.get("fix"), MAX_STEP)
        out.append({"key": _key(pid) or "item-%d" % n, "title": _text(it.get("cause"), MAX_TITLE) or "No cause given",
                    "patterns": [pid], "hosts": [], "confidence": it.get("confidence"),
                    "evidence": [it.get("evidence")] if isinstance(it.get("evidence"), str) and it.get("evidence").strip() else [],
                    "steps": [{"do": fix, "cmd": "", "why": ""}] if fix else []})
    return out


def parse(text, ids=None):
    """An answer -> {parsed, overall, causes, noise, noise_why, errors}. ids: the item ids that were asked (a pattern ACT
    names that is not one of them is dropped); each pattern in one cause only - the first cause that names it keeps it."""
    errors = []
    d, why = _object(text)
    empty = {"parsed": False, "overall": "", "causes": [], "noise": [], "noise_why": {}, "errors": [why] if why else []}
    if d is None:
        return empty
    asked = [str(i) for i in ids] if ids is not None else None
    raw = d.get("causes")
    if raw is None and isinstance(d.get("items"), list):
        raw = _from_items(d.get("items"), errors)
    if not isinstance(raw, list):
        return dict(empty, errors=["the JSON has no causes list"])
    if len(raw) > MAX_CAUSES:
        errors.append("%d causes, the first %d kept" % (len(raw), MAX_CAUSES))
    causes, seen_keys, owner = [], set(), {}
    for n, c in enumerate(raw[:MAX_CAUSES], 1):
        cz = _cause(c, n, errors)
        if cz is None:
            continue
        if cz["key"] in seen_keys:
            k2, i = "%s-%d" % (cz["key"][:MAX_KEY - 4], n), n
            while k2 in seen_keys or any(_key(x.get("key")) == k2 for x in raw[n:MAX_CAUSES] if isinstance(x, dict)):
                i += 100                                # Codex: never a key already taken (or one a later cause has)
                k2 = "%s-%d" % (cz["key"][:MAX_KEY - 6], i)
            errors.append("cause %s: the key is there twice, this one is %s" % (cz["key"], k2))
            cz["key"] = k2
        seen_keys.add(cz["key"])
        kept = []
        for p in cz["patterns"]:
            if asked is not None and p not in asked:
                errors.append("cause %s: %s is not an item asked about" % (cz["key"], p))
            elif p in owner:
                errors.append("%s in causes %s and %s: the first kept" % (p, owner[p], cz["key"]))
            elif p not in kept:
                kept.append(p)
                owner[p] = cz["key"]
        if not kept:                                    # Codex: a cause of no pattern is never a card
            errors.append("cause %s explains no item%s: dropped" % (cz["key"], " left" if cz["patterns"] else ""))
            continue
        cz["patterns"] = kept
        causes.append(cz)
    noise = []
    for p in _strs(d.get("noise")):
        p = p.strip()
        if asked is not None and p not in asked:
            errors.append("noise: %s is not an item asked about" % p)
        elif p in owner:
            errors.append("%s is in cause %s and in noise: the cause kept" % (p, owner[p]))
        elif p not in noise:
            noise.append(p)
    nw = d.get("noise_why") if isinstance(d.get("noise_why"), dict) else {}
    noise_why = dict((str(k), _text(v, MAX_LINE)) for k, v in nw.items() if str(k) in noise and _text(v, MAX_LINE))
    return {"parsed": True, "overall": _text(d.get("overall"), 1200), "causes": causes, "noise": noise, "noise_why": noise_why,
            "errors": errors}


def from_records(records, ids=None):
    """The older answers already read, one record per pattern ({id: {cause, fix, evidence, confidence[, noise]}}) -> the
    shape of parse, one cause per pattern - every record, with no cap of one answer (Codex: a report of 40+ patterns
    across batches)."""
    errors = []
    recs = records if isinstance(records, dict) else {}
    asked = [str(i) for i in ids] if ids is not None else None
    keep = sorted((str(k) for k in recs if asked is None or str(k) in asked), key=natural)
    items = [dict(recs[k], id=k) for k in keep if isinstance(recs[k], dict) and not recs[k].get("noise")]
    causes, seen = [], set()
    for n, c in enumerate(_from_items(items, errors), 1):
        cz = _cause(c, n, errors)
        if cz is None:
            continue
        k, i = cz["key"], 1
        while cz["key"] in seen:
            i += 1
            cz["key"] = "%s-%d" % (k[:MAX_KEY - 4], i)
        seen.add(cz["key"])
        causes.append(cz)
    noise = [k for k in keep if isinstance(recs[k], dict) and recs[k].get("noise")]
    noise_why = {}
    for k in noise:
        why = re.sub(r"^Noise:?\s*", "", str(recs[k].get("cause") or ""))
        if why:
            noise_why[k] = _text(why, MAX_LINE)
    return {"parsed": True, "overall": "", "causes": causes, "noise": noise, "noise_why": noise_why, "errors": errors}


# ---- ACT's labelled text as causes (1.82.0) ---------------------------------------------------------------------------------------------
# A job that runs ACT's own playbook (vendor/act: the fapolicyd denials) cannot ask for the JSON above. Its answer is text, by labels:
# BLOCKED / WHY / LEGITIMATE? / FIX / RISK (or ROOT CAUSE, CONFIDENCE) for each distinct finding, the commands in `backticks`.
# from_text reads that into the shape of parse - one cause for each finding that names an item - and leaves out what it cannot place.
_LABELS = ("blocked", "why", "legitimate", "fix", "risk", "root cause", "cause", "confidence")
_LABEL = re.compile(r"^\s*(?:[-*+\u2022]\s+|\d{1,2}[.)]\s+)?(?:\*\*|__)?\s*(BLOCKED|WHY|LEGITIMATE\??|FIX|RISK|ROOT CAUSE|CAUSE|CONFIDENCE)\s*(?:\*\*|__)?\s*[:\u2014-]\s*(?:\*\*|__)?\s*(.*)$", re.I)
_HEAD = re.compile(r"^\s*(?:#{1,6}\s+|(?:\*\*|__)\s*(?:Finding|Issue|Block|Denial)\b|(?:\d{1,2}[.)]\s+)(?:\*\*|__))", re.I)
_APPROVAL = re.compile(r"\(?\s*(?:not applied\s*[:;,-]?\s*)?(?:(?:these |this )?(?:need|needs|requires?) (?:your )?(?:isso|security|change)[^).`]*)\)?\.?|\(\s*not applied[^)`]*\)|not applied\b[^.`]*\.?", re.I)


def _plain(s):
    return " ".join(re.sub(r"\*\*|__", "", str(s)).replace("`", "").split())


def _split_blocks(text):
    blocks, cur = [], None
    for raw in str(text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _LABEL.match(line)
        head = _HEAD.match(line) and not m
        lab = m.group(1).lower().rstrip("?") if m else ""
        # a new finding: a heading; a label the finding already has; or a subject (BLOCKED, ROOT CAUSE) after its FIX
        if head or (m and (cur is None or cur["f"].get(lab) or (lab in ("blocked", "root cause", "cause") and cur["f"].get("fix")))):
            if cur and cur["f"]:
                blocks.append(cur)
            cur = {"head": _plain(re.sub(r"^\s*(?:#{1,6}\s+|\d{1,2}[.)]\s+)", "", line)) if head else "", "f": {}, "last": None, "raw": []}
            if head:
                continue
        if m:
            if cur is None:
                cur = {"head": "", "f": {}, "last": None, "raw": []}
            k = m.group(1).lower().rstrip("?")
            cur["f"][k] = [m.group(2)]
            cur["last"] = k
        elif cur is not None and cur["last"]:
            cur["f"][cur["last"]].append(line.strip())
    if cur and cur["f"]:
        blocks.append(cur)
    return blocks


def _fix_steps(fix):
    """The FIX text -> steps: each `command` with the words before it as what to do; a FIX with no command is one step of words."""
    fix = _APPROVAL.sub("", " ".join(str(fix).split())).strip()
    parts = re.split(r"`([^`]+)`", fix)
    steps, words = [], ""
    for i, part in enumerate(parts):
        if i % 2 == 0:
            words = part
            continue
        do = re.split(r"(?<=[.;:])\s+|,?\s+then\s+|\s+and\s+", words.strip())[-1] if words.strip() else ""
        do = re.sub(r"^(?:then|and|or|next|finally)\b[ ,]*", "", do.strip(" ,;:"), flags=re.I)
        do = re.sub(r"\s+(?:with|using|by running|by using|run|running|via)$", "", do.strip(" ,;:"), flags=re.I).strip()
        steps.append({"do": (do[:1].upper() + do[1:]) if do else "Run", "cmd": part.strip(), "why": ""})
    if not steps and fix:
        steps = [{"do": fix, "cmd": "", "why": ""}]
    return steps


def _names(needle, text):
    """Does text name needle (a path, a name) as a whole? Not inside a longer one: /opt/app/run is not in /opt/app/run.sh, nor
    in /usr/opt/app/run; a full stop that ends a sentence is no part of the name."""
    return re.search(r"(?<![\w/.-])" + re.escape(str(needle)) + r"(?![\w/-]|\.[\w/-])", text) is not None


def from_text(text, items):
    """ACT's labelled text -> the shape of parse. items: [{id, needles: [text that names the item, e.g. its file]}]. A finding names the
    items whose needle it mentions (one cause each); a finding that names none is left out - and listed in "unplaced", the number
    of them, so a report keeps the text of an answer it could not place."""
    errors, causes, owner, unplaced = [], [], {}, 0
    for n, b in enumerate(_split_blocks(text), 1):
        f = dict((k, _plain(" ".join(v))) for k, v in b["f"].items())
        if not f.get("fix"):
            continue
        whole = " ".join(f.values()) + " " + b["head"]
        pats = [str(i["id"]) for i in sorted(items or [], key=lambda i: -max([len(str(x)) for x in i.get("needles") or []] or [0]))
                if any(nd and _names(nd, whole) for nd in i.get("needles") or [])]
        pats = [p for p in pats if p not in owner]
        if not pats:
            unplaced += 1
            continue
        head = re.sub(r"^(?:Finding|Issue|Block|Denial)\s*\d*\s*[:.-]?\s*", "", b["head"], flags=re.I).strip(" *_:")
        title = f.get("blocked") or head or f.get("root cause") or f.get("cause") or "Cause %d" % n
        ev = [(("Legitimate? " if k == "legitimate" else "") + f[k]) for k in ("why", "root cause", "legitimate") if f.get(k) and f[k] != title]
        steps = _fix_steps(" ".join(b["f"]["fix"]))
        if f.get("risk"):
            if steps:
                steps[-1]["why"] = "Risk: " + f["risk"]
        conf = f.get("confidence")
        raw = {"key": "d-" + "-".join(pats).lower(), "title": title, "patterns": pats, "hosts": [], "confidence": conf,
               "evidence": ev, "steps": steps}
        cz = _cause(raw, n, errors)
        if cz is None:
            continue
        for p in pats:
            owner[p] = cz["key"]
        causes.append(cz)
    return {"parsed": bool(causes), "overall": "", "causes": causes, "noise": [], "noise_why": {}, "errors": errors, "unplaced": unplaced}


def merge(answers):
    """The parsed answers of several batches -> one, the same whatever their order: causes of one key joined (the union of
    their patterns and hosts, the highest confidence, the steps, title and evidence of the surest one); a pattern two causes
    name goes to the surer one; noise that a cause explains is not noise."""
    answers = [a for a in answers or [] if isinstance(a, dict)]
    by_key = {}
    for a in answers:
        for c in a.get("causes") or []:
            by_key.setdefault(c["key"], []).append(c)

    def rank(c):                      # the surest first; ties by title, steps, evidence, hosts - never by arrival (Codex)
        return (-(c.get("confidence") if c.get("confidence") is not None else -1), c.get("title") or "",
                json.dumps(c.get("steps") or [], sort_keys=True), json.dumps(c.get("evidence") or []),
                json.dumps(sorted(c.get("hosts") or [])), json.dumps(sorted(c.get("patterns") or [])))
    joined = []
    for key in sorted(by_key):
        cs = sorted(by_key[key], key=rank)
        best = dict(cs[0])
        best["patterns"] = sorted(set(p for c in cs for p in c.get("patterns") or []), key=natural)
        best["hosts"] = sorted(set(h for c in cs for h in c.get("hosts") or []), key=natural)
        confs = [c.get("confidence") for c in cs if c.get("confidence") is not None]
        best["confidence"] = max(confs) if confs else None
        joined.append(best)
    owner, errors = {}, []
    for c in sorted(joined, key=lambda c: (rank(c), c["key"])):
        kept = []
        for p in c["patterns"]:
            if p in owner:
                errors.append("%s in causes %s and %s: the surer kept" % (p, owner[p], c["key"]))
            else:
                owner[p] = c["key"]
                kept.append(p)
        c["patterns"] = kept
    joined = [c for c in joined if c["patterns"]]
    noise = sorted(set(p for a in answers for p in a.get("noise") or [] if p not in owner), key=natural)
    noise_why = {}
    for a in sorted(answers, key=lambda a: json.dumps(a.get("noise_why") or {}, sort_keys=True)):
        for k, v in (a.get("noise_why") or {}).items():
            if k in noise and k not in noise_why:
                noise_why[k] = v
    overall = " ".join(a.get("overall") for a in sorted(answers, key=lambda a: a.get("overall") or "") if a.get("overall"))
    errors = sorted(set(e for a in answers for e in a.get("errors") or [])) + sorted(set(errors))
    return {"parsed": any(a.get("parsed") for a in answers), "overall": overall, "causes": joined, "noise": noise,
            "noise_why": noise_why, "errors": errors}
