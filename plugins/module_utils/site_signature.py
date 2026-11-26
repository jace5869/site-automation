# -*- coding: utf-8 -*-
"""Signatures (1.136.0): what stays the same from one occurrence of a line, a finding or a host's set of findings to the
next - the numbers, ids, addresses and times taken out, and the host's own names when they are given. The log analysis'
rules (log_signature, plugins/filter/log_filters.py) live here, so the jobs that group hosts by what they found
(roles/site_act/tasks/run.yml, fapolicyd) use the same ones.

Standard library only, Python 3.6. Loaded by path (as site_time.py is): plugins/filter/log_filters.py and
plugins/filter/act_group_filters.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import json
import re

RULES = [
    (re.compile(r"^\s*\[?\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\]?\s*"), ""),     # a leading ISO time
    (re.compile(r"^\s*[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d\s+"), ""),                                  # a leading syslog time
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d(\.\d+)?)?(Z|[+-]\d\d:?\d\d)?\b"), "<time>"),
    (re.compile(r"\b\d\d:\d\d:\d\d(\.\d+)?\b"), "<time>"),
    (re.compile(r"\b(\d{1,3}\.){3}\d{1,3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"\b([0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I), "<ip>"),
    (re.compile(r"\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.I), "<mac>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\"[^\"]{0,200}\"|'[^']{0,200}'"), "<q>"),
    (re.compile(r"\[\d+\]"), "[<n>]"),
    (re.compile(r"\b\d+(\.\d+)*\b"), "<n>"),
    (re.compile(r"(<n>[\s,]*){2,}"), "<n> "),
    (re.compile(r"\s+"), " "),
]
HOST = "<host>"


def _names(names):
    """The names to take out, longest first (web01.corp.example.mil before web01), each once."""
    out = []
    for n in names or []:
        n = str(n or "").strip()
        if n and n.lower() not in [x.lower() for x in out]:
            out.append(n)
    return sorted(out, key=len, reverse=True)


def signature(text, names=None):
    """A line's signature: the words that stay the same from one occurrence to the next (lower case, at most 200
    characters). `names` (a host's own names) become <host> first, as whole words."""
    s = str(text or "")
    for n in _names(names):
        s = re.sub(r"(?<![\w.-])%s(?![\w-])" % re.escape(n), HOST, s, flags=re.I)
    for rx, rep in RULES:
        s = rx.sub(rep, s)
    return s.strip().lower()[:200]


def group(records, text="message", obj="host", examples=3, cap=300, names=None):
    """Records that say the same thing, together: [{sig, objects, examples, count}] in the order first seen. objects:
    the records' `obj` values, sorted and each once; examples: the first `examples` texts, each cut at `cap`."""
    out, at = [], {}
    for r in records or []:
        if not isinstance(r, dict):
            continue
        sig = signature(r.get(text), names)
        if sig not in at:
            at[sig] = len(out)
            out.append({"sig": sig, "objects": [], "examples": [], "count": 0})
        g = out[at[sig]]
        g["count"] += 1
        o = r.get(obj)
        if o not in (None, "") and str(o) not in g["objects"]:
            g["objects"].append(str(o))
        if len(g["examples"]) < examples:
            g["examples"].append(str(r.get(text) or "")[:cap])
    for g in out:
        g["objects"].sort()
    return out


_QUOTED = re.compile(r'"([^"\n]{1,200})"|\'([^\'\n]{1,200})\'')


def _sig_has_name(text, names):
    t = str(text).lower()
    return any(str(n).lower() in t for n in (names or []) if n not in (None, ""))


_PORTS = re.compile(r"(?i)(?:\bport\s*|(?<=[A-Za-z0-9\]]):)(\d{1,5})\b")


def finding_lines(findings, names=None):
    """A host's findings as signatures, sorted and each once: '[severity] check: summary', its names taken out."""
    lines = set()
    for f in findings or []:
        if isinstance(f, dict):
            text = "%s %s" % (f.get("summary") or "", f.get("hint") or "")
            # 1.136.0 (Codex P2): the ports stay (443 and 636 are two services), and the finding's id and its hint (the
            # read-only command ACT is shown): two hosts are one group only when the same things failed the same way
            ports = sorted(set(_PORTS.findall(text)))
            line = signature("[%s] %s: %s" % (str(f.get("severity") or "").upper(), f.get("check") or "", f.get("summary") or ""),
                             names)
            if f.get("id") not in (None, ""):
                line += " id=" + signature(str(f.get("id")), names)
            if f.get("hint"):
                line += " hint=" + signature(str(f.get("hint")), names)
            if ports:
                line += " ports=" + ",".join(ports)
            # the quoted names (a unit 'postgresql', a mount "/var") stay too, the host's own names out (Neon P3)
            quoted = sorted(set(signature(a or b, names) if _sig_has_name(a or b, names) else (a or b)
                                for a, b in _QUOTED.findall(text)))
            if quoted:
                line += " quoted=" + json.dumps(quoted)
            lines.add(line)
    return sorted(lines)


def finding_set(findings, names=None):
    """One signature for a host's whole set of findings (sha256 of finding_lines): two hosts with the same one found the
    same things. '' for no findings."""
    lines = finding_lines(findings, names)
    return hashlib.sha256(json.dumps(lines).encode("utf-8")).hexdigest() if lines else ""
