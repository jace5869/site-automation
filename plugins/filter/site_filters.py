"""Filters for this repository (loaded through ansible.cfg: filter_plugins = plugins/filter).
Only the Python standard library is used, so they work in every execution environment.

from_csv        the text of a CSV file -> a list of dictionaries, one per row, keyed by the
                header row. Spaces around headers/values and an Excel byte-order mark are
                removed; empty rows are skipped.
                    {{ lookup('ansible.builtin.file', 'poam/poam.csv') | from_csv }}

days_until      a date written in any of the given formats -> whole days from today (negative =
                in the past), or None when it is not a date in any of those formats.
                    {{ '10/31/2026' | days_until(['%Y-%m-%d', '%m/%d/%Y']) }}

site_result     the result a Windows script printed (the JSON after its last
                '###SITE-JSON### ' line), from a registered result's stdout_lines;
                {} (or the given default) when there is none.
                    {{ _out.stdout_lines | default([]) | site_result }}
"""
import csv
import datetime
import json
import io


def from_csv(text, delimiter=","):
    if text is None:
        return []
    text = str(text).lstrip("﻿")
    rows = []
    for row in csv.DictReader(io.StringIO(text), delimiter=delimiter):
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        if any(clean.values()):
            rows.append(clean)
    return rows


def days_until(value, formats=("%Y-%m-%d",)):
    text = str(value or "").strip()
    if not text:
        return None
    if isinstance(formats, str):
        formats = [formats]
    for fmt in formats:
        try:
            day = datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return (day - datetime.date.today()).days
    return None


def site_result(lines, default=None):
    """The result a Windows script printed: the JSON after the LAST '###SITE-JSON### ' line.

    `lines` is a registered result's stdout_lines (a string is split into lines). Anything else
    the script printed is ignored. No such line, or not JSON: `default` ({} if not given).
    """
    if default is None:
        default = {}
    if isinstance(lines, str):
        lines = lines.splitlines()
    marker = "###SITE-JSON### "
    for line in reversed(list(lines or [])):
        line = str(line)
        if line.startswith(marker):
            try:
                value = json.loads(line[len(marker):])
            except ValueError:
                return default
            return default if value is None else value
    return default


class FilterModule(object):
    def filters(self):
        return {"from_csv": from_csv, "days_until": days_until, "site_result": site_result}
