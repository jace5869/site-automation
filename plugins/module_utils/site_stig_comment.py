# -*- coding: utf-8 -*-
"""1.69.1: the comment on a STIG Manager review that site-automation writes - the STIG recheck's (workflow 37,
roles/stigman_update) and the checklist import's (workflow 40, roles/stigman_ckl_import). One rule for both:

- our line first, with today's date; a person's comment kept below it, after one blank line - never lost;
- a line of ours from an earlier run (the recheck's, a carry forward's or an import's) is never kept beside a new one:
  it is the one replaced.

A line of ours is a paragraph that starts with one of OURS. what_it_said() reads the result our line gave (its last
words: '- Open.', '- still Open.', '- Not a Finding.', '- Not Applicable.'), so a review we last wrote as Open that
is Not a Finding now was changed by a person after us (the recheck lists it when the scan still says Open).

protected(): a review whose detail or comment says one of stigman_protect_words (RA, Risk Acceptance, POAM, POA&M) is
left as it is by every writer - the recheck's update and the checklist import."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

OURS = ("Set by the STIG recheck (site-automation)", "Carried forward by the STIG recheck (site-automation)",
        "Imported from the checklist ")
MAX = 32767                     # STIG Manager's comment
_PARA = re.compile(r"\n[ \t]*\n")
_SAID = (("still Open.", "fail"), ("Not a Finding.", "pass"), ("Not Applicable.", "notapplicable"), ("Open.", "fail"))


def split(comment):
    """A review's comment -> (our line from an earlier run, or '' - the first of ours; the rest: a person's, as written)."""
    text = str(comment or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "", ""
    paras = [p.strip() for p in _PARA.split(text) if p.strip()]
    mine = [p for p in paras if p.startswith(OURS)]
    rest = [p for p in paras if not p.startswith(OURS)]
    return (mine[0] if mine else ""), "\n\n".join(rest)


def merged(line, current):
    """Our new line, first; the person's comment of current (every line of ours left out) below it."""
    line = str(line or "").strip()
    _, theirs = split(current)
    out = (line + "\n\n" + theirs) if (line and theirs) else (line or theirs)
    return out[:MAX]


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
