# -*- coding: utf-8 -*-
"""Report heads (1.82.0): a report's title as its NAME alone, a line of COUNTS (each with its status, the worst first), a muted SCOPE line, and a
short email SUBJECT. report_head(report) works them out from the report's own title and subtitle, so every report gets the same head; a report
that knows better sets any of head_name, head_counts ([[number, words, status], ...]), head_scope ([text, ...]) or head_subject itself and that one is kept.
The rules (docs/WEB_REPORTS.md, "The head of a report"):
  name    the title before its first ": " with a trailing "report" left off ("STIG drift report: ..." -> "STIG drift");
  counts  2 to 4 of the title's numbers with their words ("5 critical", "7 warnings"), never a 0 unless everything is 0, plural words (no "(s)");
  scope   what was read and when: what the title says besides counts, then the subtitle;
  subject name and the top counts, 60 characters at most.
Pure functions, loaded by plugins/filter/site_filters.py (report_head, report_plural); tests/test_report_head.py runs every report fixture of the tests through them."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

MAX_CHIPS = 4
SUBJECT_MAX = 60
SUBJECT_WORDS_MAX = 100       # a title with no count ("Mail relay whitelist - waiting for approval: add 192.0.2.7") keeps what it says in the subject
_SEV = {"critical": 0, "warning": 1, "info": 2, "ok": 3}

# the words that give a count its status: a count is read by its words, never by its number
_CRIT = re.compile(r"\b(critical|fail(?:ed|ures?)?|errors?|expired|breach(?:ed|es)?|down|unreachable|not running|not up|not operable|full|broken|"
                   r"compromised|not compliant|denied|open|locked out|late|not launched|could not)\b", re.I)
_WARN = re.compile(r"\b(warnings?|to (?:fix|look at|log off|delete|approve|review|upgrade|move|assign|watch|write|check)|differ|slow|short|held back|"
                   r"nearly|too big|too small|without|not (?:read|connected|checked|drafted|scanned|supported|found)|stale|old|older|pending|new|"
                   r"changed|more|auditd|permissive|hosts? with|failed logons?|waiting)\b", re.I)
_OK = re.compile(r"\b(ok|ready|healthy|unchanged|passed|good|written|created|fixed|matched|within limits|compliant|valid|up)\b", re.I)

_PAREN_S = re.compile(r"([A-Za-z]+)\((s|es)\)")
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?%?)")
_LEAD = re.compile(r"^(\d[\d,]*(?:\.\d+)?%?|no|none)\s+(.+)$", re.I)
_CUT = re.compile(r"\s+(?:on|in|across|from|since|of)\s+|\s+\(")
_TRAIL = re.compile(r"^([A-Za-z][A-Za-z -]{1,28}?)\s+(\d[\d,]*)$")          # "deleted 4": the number after its word
_ON_N = re.compile(r"^(.*\S)\s+on\s+(\d[\d,]*)$")                  # "fapolicyd not running on 1": the number last
_STOP = ("of", "on", "in", "at", "to", "for", "and", "or", "by", "from", "with")
_OF = re.compile(r"^of\s+\d[\d,]*\s*")
_TAIL_PAREN = re.compile(r"\s*\(([^()]*)\)\s*$")
_WORD_REPORT = re.compile(r"\s+report$", re.I)


def _int(s):
    try:
        return float(str(s).replace(",", "").rstrip("%"))
    except ValueError:
        return None


def plural(text):
    """'3 finding(s) on 2 of 2 host(s)' -> '3 findings on 2 of 2 hosts'; the form follows the number just before the word (1 -> 'host'); no number -> plural."""
    text = str(text)

    def one(m):
        before = text[:m.start()]
        nums = _NUM.findall(before[-24:])
        n = _int(nums[-1]) if nums else None
        return m.group(1) + ("" if n == 1 else m.group(2))
    return _PAREN_S.sub(one, text)


def _split_top(s, seps):
    """Split s on any of seps (strings) outside round brackets."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        hit = None
        if depth == 0:
            for sp in seps:
                if s.startswith(sp, i):
                    hit = sp
                    break
        if hit:
            out.append(cur)
            cur, i = "", i + len(hit)
            continue
        cur += ch
        i += 1
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _status_of(words, doc_status, n=None):
    if n == 0:
        return "ok"                       # nothing of it: good
    if _CRIT.search(words):
        st = "critical"
    elif _WARN.search(words):
        st = "warning"
    elif _OK.search(words):
        st = "ok"
    else:
        st = doc_status if doc_status in ("critical", "warning") else "info"      # its words say nothing: the report's own state
    if doc_status in ("ok", "info") and st in ("critical", "warning"):
        st = doc_status
    return st


def _name(t):
    t = " ".join(str(t or "").split())
    short = _WORD_REPORT.sub("", t).strip()
    return short if len(short.split()) >= 2 else t         # "STIG drift report" -> "STIG drift"; "Disk report" stays


def _chip_words(rest, n):
    """A count's words, cut where the scope begins ('findings on 2 of 2 hosts' -> 'findings', 'on 2 of 2 hosts'); the plural form follows n."""
    lead = _OF.match(rest)               # "1 of 4 hosts changed": the "of 4" belongs to the count
    k = lead.end() if lead else 0
    m = _CUT.search(rest, k)
    if m and m.start() > 0:
        return rest[:m.start()].strip(), rest[m.start():].strip()
    return rest.strip(), ""


def _scope_text(x):
    x = plural(x).strip().rstrip(".")
    m = _TAIL_PAREN.match(x) if x.startswith("(") else None
    if x.startswith("(") and x.endswith(")") and x.count("(") == 1:
        x = x[1:-1]
    return x.strip()


SCOPE_MAX = 80


def _short_scope(sub):
    """A subtitle's first clause as the scope line: parentheses and everything after ' - ' belong to the method (end of the report)."""
    first = re.sub(r"\s*\([^)]*\)", "", sub.split(" - ", 1)[0]).strip().rstrip(".,;:")
    if len(first) > SCOPE_MAX:
        first = first[:SCOPE_MAX].rsplit(",", 1)[0] if "," in first[:SCOPE_MAX] else first[:SCOPE_MAX].rsplit(" ", 1)[0]
    return first


def _parse(title, status):
    """-> name, chips [[n, words, status]], scope [text], plain [text] (what the title says that is no count and short)."""
    t = " ".join(str(title or "").split())
    scope, chips, plain = [], [], []
    name, rest, strip = t, "", False
    if ": " in t:
        name, rest = t.split(": ", 1)
        strip = True
    else:
        m = _TAIL_PAREN.search(t)
        if m and m.start() > 0:
            name, rest = t[:m.start()], ""
            scope.append(m.group(1).strip())
            strip = True
    if not rest:
        return (_name(name) if strip else name), chips, scope, plain
    groups = _split_top(rest, [" - ", u" – ", u" — "])
    for gi, g in enumerate(groups):
        for seg in _split_top(g, [", ", "; "]):
            seg = plural(seg).strip().rstrip(".")
            m = _LEAD.match(seg)
            if gi == 0 and m:
                n = 0 if m.group(1).lower() in ("no", "none") else (1 if m.group(1).lower() == "one" else _int(m.group(1)))
                if n is None:
                    scope.append(_scope_text(seg))
                    continue
                words, extra = _chip_words(m.group(2), n)
                if m.group(1).endswith("%"):
                    words, n = m.group(2), m.group(1)
                if extra:
                    scope.append(_scope_text(extra))
                chips.append([m.group(1) if m.group(1).endswith("%") else (int(n) if float(n) == int(n) else n), words, _status_of(words, status, n), n])
            elif gi == 0 and _ON_N.match(seg) and len(_ON_N.match(seg).group(1).split()) <= 5:
                words, n = _ON_N.match(seg).group(1).strip(), _int(_ON_N.match(seg).group(2))
                chips.append([int(n), words, _status_of(words, status, n), n])
            elif gi == 0 and _TRAIL.match(seg) and _int(_TRAIL.match(seg).group(2)) is not None and _TRAIL.match(seg).group(1).split()[-1].lower() not in _STOP:
                n = _int(_TRAIL.match(seg).group(2))
                words = _TRAIL.match(seg).group(1).strip()
                chips.append([int(n), words, _status_of(words, status, n), n])
            elif gi == 0 and len(seg.split()) <= 6 and len(seg) <= 40 and not seg.startswith("(") and ": " not in seg:
                plain.append(seg)             # what the title says in words (READY, a name): a chip when the title has no count
            else:
                scope.append(_scope_text(seg))
    return (_name(name) if strip else name), chips, scope, plain


def _chips_final(chips, plain, status):
    live = [c for c in chips if c[3] != 0]
    if not live:
        live = [[("no" if c[0] == 0 else c[0])] + c[1:] for c in chips[:1]]         # everything is 0: say so once ("no denials")
    live = sorted(live, key=lambda c: _SEV.get(c[2], 2))     # the worst first (a stable sort keeps the title's order)
    kept = [[c[0], c[1], c[2]] for c in live[:MAX_CHIPS]]
    dropped = ["%s %s" % (c[0], c[1]) for c in live[MAX_CHIPS:]]
    if not kept and plain:                         # no number in the title: its short words as chips (two at most), in the report's own state
        kept = [["", x, status if status in _SEV else "info"] for x in plain[:2]]
        plain = plain[2:]
    return kept, dropped, plain


def _chip_text(c):
    return ("%s %s" % (c[0], c[1])).strip()


def _subject(name, chips, fallback):
    if not chips:
        s = name if not fallback or len(name) + 2 + len(fallback) > SUBJECT_MAX else "%s: %s" % (name, fallback)
    else:
        s = name + ": " + ", ".join(_chip_text(c) for c in chips[:3])
        while len(s) > SUBJECT_MAX and len(chips) > 1:
            chips = chips[:-1]
            s = name + ": " + ", ".join(_chip_text(c) for c in chips[:3])
    if len(s) > SUBJECT_MAX:
        s = s[:SUBJECT_MAX - 1].rstrip(" ,;:-") + u"…"
    return s


def report_head(report):
    """-> {name, counts: [{n, words, status, text}], scope: [text], scope_text, subject, counts_text} for a report. Explicit report keys win:
    head_name, head_counts ([[n, words, status]] or [{n, words, status}]), scope (a list or one string), subject."""
    r = report if isinstance(report, dict) else {}
    status = str(r.get("status") or "info")
    name, chips, scope, plain = _parse(r.get("title"), status)
    kept, dropped, plain = _chips_final(chips, plain, status)
    sub = plural(" ".join(str(r.get("subtitle") or "").split())).strip()
    short = _short_scope(sub) if sub else ""
    scope = [x for x in scope if x] + [x for x in plain if x] + dropped + ([short] if short else [])
    if r.get("head_name"):
        name = " ".join(str(r["head_name"]).split())
    if r.get("head_counts") is not None:
        kept = []
        for c in r.get("head_counts") or []:
            if isinstance(c, dict):
                c = [c.get("n"), c.get("words"), c.get("status")]
            kept.append([c[0], str(c[1] or ""), c[2] if len(c) > 2 and c[2] in _SEV else "info"])
    if r.get("head_scope") is not None:
        s = r["head_scope"]
        scope = [str(x) for x in (s if isinstance(s, (list, tuple)) else [s]) if x]
        short = ""
    tail = ""
    if not kept:
        tail = _scope_text(": ".join(str(r.get("title") or "").split(": ")[1:])) if ": " in str(r.get("title") or "") else ""
    if r.get("head_subject"):
        subject = " ".join(str(r["head_subject"]).split())                  # one line: it is a mail header
    elif kept and any(c[0] not in ("", None) for c in kept):
        subject = _subject(name, kept, tail)
    else:                                                                    # no count in it: the title as it is (what it names is what the reader needs), cut at SUBJECT_WORDS_MAX
        full = plural(" ".join(str(r.get("title") or "").split())) or name
        subject = full if len(full) <= SUBJECT_WORDS_MAX else full[:SUBJECT_WORDS_MAX - 1].rstrip(" ,;:-") + u"\u2026"
    counts = [{"n": c[0], "words": c[1], "status": c[2], "text": _chip_text(c)} for c in kept]
    method = " ".join(str(r["head_method"]).split()) if r.get("head_method") is not None else (sub if sub and sub != short else "")
    return {"method": method, "name": name, "counts": counts, "scope": scope, "scope_text": u" · ".join(scope),
            "subject": subject, "counts_text": u" · ".join(c["text"] for c in counts)}

