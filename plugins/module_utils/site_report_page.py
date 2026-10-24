# -*- coding: utf-8 -*-
"""A report kept for the job "Report website - copy" (roles/site_email, report_web_copy): what a report job would have
written on the report website - its file names, address, data and index line - packed small enough to travel as its
job artifact site_report_page (base64 of gzip of JSON), and read back by the copy job (site_report_copy, and the
filter report_page_unpack in plugins/filter/site_filters.py, which must read it the same way). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import copy
import gzip
import json

VERSION = 1
KEEP_AT_LEAST = 10            # rows (or lines) a cut table keeps


def _text(doc):
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return base64.b64encode(gzip.compress(raw, 9, mtime=0)).decode("ascii")


def _items(section):
    """The section's rows (a table) or lines (a list): (key, list)."""
    if isinstance(section.get("rows"), list) and section["rows"]:
        return "rows", section["rows"]
    if isinstance(section.get("lines"), list) and section["lines"]:
        return "lines", section["lines"]
    return "", []


def pack(doc, max_bytes):
    """(text, cut): doc packed in at most max_bytes characters - the longest tables (or lists) of doc['data']['report'] halved
    until it fits, each cut one saying so on the page; cut = [(section title, kept, of)]. text '' when it does not fit even
    then. doc itself is not changed."""
    doc = dict(doc, v=VERSION)
    text = _text(doc)
    if len(text) <= max_bytes:
        return text, []
    doc = copy.deepcopy(doc)
    report = (doc.get("data") or {}).get("report") or {}
    sections = [s for s in report.get("sections") or [] if isinstance(s, dict)]
    total = {}
    while len(text) > max_bytes:
        big = max(sections, key=lambda s: len(_items(s)[1]), default=None)
        key, items = _items(big) if big is not None else ("", [])
        if len(items) <= KEEP_AT_LEAST:
            return "", sorted((s.get("title") or "", len(_items(s)[1]), n) for s, n in total.values())
        total.setdefault(id(big), (big, len(items)))
        keep = max(KEEP_AT_LEAST, len(items) // 2)
        big[key] = items[:keep]
        for k in ("row_status", "cell_status"):
            if key == "rows" and isinstance(big.get(k), list):
                big[k] = big[k][:keep]
        text = _text(doc)
    cut = []
    for s, n in total.values():
        kept = len(_items(s)[1])
        note = "This page shows the first %d of %d %s (report_web_copy_max_kb)." % (kept, n, "rows" if s.get("rows") else "lines")
        s["text"] = ((str(s.get("text")).rstrip() + " ") if s.get("text") else "") + note
        cut.append((s.get("title") or "", kept, n))
    text = _text(doc)
    return text, sorted(cut)


def unpack(text):
    """The doc from pack's text; ValueError says why it cannot be read."""
    try:
        doc = json.loads(gzip.decompress(base64.b64decode(str(text), validate=True)).decode("utf-8"))
    except Exception as e:                      # noqa: BLE001 - whatever it is, it is not a kept report
        raise ValueError("not a kept report (%s)" % (str(e) or type(e).__name__))
    if not isinstance(doc, dict) or doc.get("v") != VERSION:
        raise ValueError("a kept report of another version (%s): update the copy job's project"
                         % (doc.get("v") if isinstance(doc, dict) else type(doc).__name__))
    files, data = doc.get("files"), doc.get("data")
    if not isinstance(files, dict) or not str(files.get("page") or "").endswith(".html"):
        raise ValueError("a kept report without its page's name")
    if not isinstance(data, dict) or not isinstance(data.get("report"), dict) or not isinstance(doc.get("entry"), dict):
        raise ValueError("a kept report without its report")
    return doc
