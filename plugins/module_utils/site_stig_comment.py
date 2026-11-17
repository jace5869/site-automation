# -*- coding: utf-8 -*-
"""1.69.1: the comment on a STIG Manager review that site-automation writes - the STIG recheck's (workflow 37,
roles/stigman_update) and the checklist import's (workflow 40, roles/stigman_ckl_import). One rule for both:

- our line first, with today's date; a person's comment kept below it, after one blank line - never lost;
- a line of ours from an earlier run (the recheck's, a carry forward's or an import's) is never kept beside a new one:
  it is the one replaced.

A line of ours is one sentence, as site-automation writes it (_OURS: from its first words through its last - '... -
Open.', '... (check_stig_recheck_carry_forward).', '... approved in AAP on 2026-11-17 - Open.'). Only that sentence is
taken out of a review's comment: everything else - a person's text on the same line, after a single line end, anywhere -
is kept byte for byte (1.69.1c, Codex P1). what_it_said() reads the result our line gave, so a review we last wrote as
Open that is Not a Finding now was changed by a person after us (the recheck lists it when the scan still says Open).
merged() never cuts a person's text: a comment that would be longer than STIG Manager's 32767 characters is None - the
writer leaves that review and says so (1.69.1c, Codex P2).

protected(): a review whose detail or comment says one of stigman_protect_words (RA, Risk Acceptance, POAM, POA&M) is
left as it is by every writer - the recheck's update and the checklist import."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

OURS = ("Set by the STIG recheck (site-automation)", "Carried forward by the STIG recheck (site-automation)",
        "Imported from the checklist ")
MAX = 32767                     # STIG Manager's comment
_SAID = (("still Open.", "fail"), ("Not a Finding.", "pass"), ("Not Applicable.", "notapplicable"), ("Open.", "fail"))
_RESULT = r"(?:still Open|Open|Not a Finding|Not Applicable)"
# our sentences, exactly - on one line, from the first words through the last
_OURS = re.compile(
    r"Set by the STIG recheck \(site-automation\), approved in AAP: [^\r\n]*? - " + _RESULT + r"\."
    r"|Carried forward by the STIG recheck \(site-automation\), approved in AAP: [^\r\n]*?\(check_stig_recheck_carry_forward\)\."
    r"|Imported from the checklist [^\r\n]*? by site-automation, (?:approved in AAP|no approval \(stigman_ckl_import_auto\))"
    r"(?: on \d{4}-\d{2}-\d{2})?(?: - " + _RESULT + r")?\.")
_GAP_END = re.compile(r"\r?\n\r?\n\Z")       # the blank line before a sentence of ours at the end
_GAP_START = re.compile(r"\r?\n\r?\n")        # the blank line after one at the start (or between two paragraphs)


def split(comment):
    """A review's comment -> (our sentence from an earlier run, or '' - the first one; the comment without any sentence of
    ours, everything else as written). The blank line site-automation put beside its sentence goes with it."""
    text = "" if comment is None else str(comment)
    first = ""
    while True:
        m = _OURS.search(text)
        if not m:
            break
        first = first or m.group(0)
        before, after = text[:m.start()], text[m.end():]
        gb, ga = _GAP_END.search(before), _GAP_START.match(after)
        if ga and not before.strip():               # ours first, then a blank line: as merged() writes it
            text = before + after[ga.end():]
        elif gb and not after.strip():              # theirs, a blank line, ours last (an import before 1.69.1)
            text = before[:gb.start()] + after
        elif gb and ga:                             # between two paragraphs: one blank line stays
            text = before + after[ga.end():]
        else:                                       # on a line with a person's text: only the sentence goes
            text = before + after
    return first, text


def merged(line, current):
    """Our new line, first; the review's comment below it, without any sentence of ours, as written - or None when that
    would be longer than STIG Manager keeps (MAX): a person's text is never cut."""
    line = str(line or "").strip()
    _, theirs = split(current)
    if not theirs.strip():
        theirs = ""
    out = (line + "\n\n" + theirs) if (line and theirs) else (line or theirs)
    return out if len(out) <= MAX else None


# 1.69.1: a review that says one of these in its detail or comment is never written by site-automation (stigman_protect_words)
PROTECT_WORDS = ["RA", "Risk Acceptance", "POAM", "POA&M"]


def _word_re(word):
    """A protect word -> its pattern: case-insensitive, a whole word or phrase (RA is not RAID, grant, parameter or
    ORACLE); any spaces inside a phrase; spaces around & allowed (POA & M); a word longer than 2 letters also with an s
    (POA&Ms)."""
    out = []
    for ch in str(word).strip():
        if ch.isspace():
            if not out or out[-1] != r"\s+":
                out.append(r"\s+")
        elif ch == "&":
            out.append(r"\s*&\s*")
        else:
            out.append(re.escape(ch))
    if not out:
        return None
    plural = "s?" if len(str(word).strip()) > 2 else ""       # POA&Ms, POAMs, Risk Acceptances - not RAs (RAS is a server)
    return re.compile(r"(?<![A-Za-z0-9])" + "".join(out) + plural + r"(?![A-Za-z0-9])", re.IGNORECASE)


def protected(words, detail, comment):
    """A review's detail and comment -> why it is protected ("'POA&M' in its comment"), or '' - the first protect word found,
    as the setting names it. words None: PROTECT_WORDS; [] = none."""
    for w in (PROTECT_WORDS if words is None else words) or []:
        rx = _word_re(w)
        if rx is None:
            continue
        for where, text in (("detail", detail), ("comment", comment)):
            if text and rx.search(str(text)):
                return "'%s' in its %s" % (str(w).strip(), where)
    return ""


def what_it_said(line):
    """Our line -> the result it wrote (fail, pass, notapplicable) - '' for none (an import's line, or not ours)."""
    line = str(line or "").strip()
    if not line.startswith(OURS):
        return ""
    for end, res in _SAID:
        if line.endswith(end):
            return res
    return ""
