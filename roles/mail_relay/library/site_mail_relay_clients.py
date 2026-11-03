#!/usr/bin/python
# -*- coding: utf-8 -*-
"""On a mail relay: which client addresses sent mail through Postfix, and when they last did - from the mail logs
(/var/log/maillog, its rotated copies, .gz too, and an archive folder of them) or, without them, from the journal. Read-only. For the job that
finds the whitelisted addresses that sent nothing for months (playbooks/mail_relay_unused.yml). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_mail_relay_clients
short_description: Which clients sent mail through Postfix, and when they last did, from the mail logs (read-only)
description:
  - Reads Postfix's lines in the mail log files (C(paths), rotated copies included - gzip, bzip2 or xz) or, when none is
    there (C(journal=auto)), in the journal (journalctl).
  - A pattern that names a folder (an archive of old mail logs, /var/log/maillog_archive): every file in it and in its
    folders whose path has "mail" in it (any case) - so an archive of other logs beside them is not read. ** in a
    pattern goes into folders too.
  - A client B(sent mail) when smtpd accepted a message from it - its C(client=NAME[ADDRESS]) line. A connection
    without a message (C(connect from NAME[ADDRESS]) only) is counted apart: a scanner or a monitor connects and
    sends nothing.
  - Says how far back the logs go - the oldest line read - so a job can tell whether they cover the time asked.
  - A syslog line without a year (C(Nov  3 06:15:01)) gets the year from its file: the date in its name
    (C(maillog-20260101), as logrotate's dateext names it - an archive's copies keep it), else the file's last change
    (a line of a later month than that is of the year before).
options:
  paths: {description: "The log files or folders (shell patterns).", type: list, elements: str, default: ['/var/log/maillog*']}
  journal: {description: "auto (when no file matches), true (as well) or false.", type: str, default: auto, choices: [auto, 'true', 'false']}
  identifier: {description: "The journal's identifier of Postfix's smtpd.", type: str, default: postfix/smtpd}
  max_connect_only: {description: At most this many clients that only connected are returned., type: int, default: 500}
author: site automation
'''

RETURN = r'''
clients:
  description: "{address: {sent, last_sent, first_sent, connects, last_connect}} - every client that sent mail, and up
    to max_connect_only that only connected (times as YYYY-MM-DDTHH:MM:SSZ, UTC; '' = never)."
  type: dict
  returned: always
oldest:
  description: The time of the oldest line read ('' = none) - how far back the logs go.
  type: str
  returned: always
newest:
  description: The time of the newest line read.
  type: str
  returned: always
sources:
  description: "Each file or journal read: [{source, lines, oldest, newest, error}]."
  type: list
  returned: always
connect_only_left_out:
  description: Clients that only connected and were not returned (over max_connect_only).
  type: int
  returned: always
'''

import bz2
import calendar
import glob
import gzip
import lzma
import os
import re
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule

MONTHS = dict((m, i) for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1))
ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?")
SYSLOG = re.compile(r"^([A-Z][a-z]{2}) +(\d{1,2}) (\d\d):(\d\d):(\d\d) ")
SENT = re.compile(r"postfix[^:]*/smtpd\[\d+\]: [0-9A-Za-z]+: client=[^\s\[]*\[(\d{1,3}(?:\.\d{1,3}){3})\]")
CONNECT = re.compile(r"postfix[^:]*/smtpd\[\d+\]: connect from [^\s\[]*\[(\d{1,3}(?:\.\d{1,3}){3})\]")


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


def stamp(line, ref):
    """The line's time in seconds (UTC), or None. ref: the time its file last changed (for a syslog line's year)."""
    m = ISO.match(line)
    if m:
        y, mo, d, h, mi, s, tz = m.groups()
        t = calendar.timegm((int(y), int(mo), int(d), int(h), int(mi), int(s)))
        if tz and tz != "Z":
            sign = -1 if tz[0] == "-" else 1
            tz = tz[1:].replace(":", "")
            t -= sign * (int(tz[:2]) * 3600 + int(tz[2:]) * 60)
        return t
    m = SYSLOG.match(line)
    if m and m.group(1) in MONTHS:
        r = time.localtime(ref)
        year = r.tm_year - (1 if (MONTHS[m.group(1)], int(m.group(2))) > (r.tm_mon, r.tm_mday + 1) else 0)
        try:
            return time.mktime((year, MONTHS[m.group(1)], int(m.group(2)), int(m.group(3)), int(m.group(4)),
                                int(m.group(5)), 0, 0, -1))      # the relay's local time, as syslog wrote it
        except (OverflowError, ValueError):
            return None
    return None


class Seen(object):
    def __init__(self):
        self.clients = {}
        self.oldest = None
        self.newest = None

    def when(self, t):
        if t is None:
            return
        self.oldest = t if self.oldest is None or t < self.oldest else self.oldest
        self.newest = t if self.newest is None or t > self.newest else self.newest

    def line(self, line, ref):
        """One log line -> (its time or None, counted)."""
        sent = SENT.search(line) if "client=" in line else None
        conn = CONNECT.search(line) if sent is None and "connect from" in line else None
        if sent is None and conn is None:
            return None
        t = stamp(line, ref)
        c = self.clients.setdefault((sent or conn).group(1), {"sent": 0, "last_sent": 0, "first_sent": 0, "connects": 0,
                                                              "last_connect": 0})
        if sent:
            c["sent"] += 1
            if t and t > c["last_sent"]:
                c["last_sent"] = t
            if t and (not c["first_sent"] or t < c["first_sent"]):
                c["first_sent"] = t
        else:
            c["connects"] += 1
            if t and t > c["last_connect"]:
                c["last_connect"] = t
        return t


def log_files(patterns):
    """The files the patterns name, sorted; a folder one names: the files in it and in its folders with "mail" in
    their path (a folder of old mail logs - /var/log/maillog_archive - not a folder of every log)."""
    out = set()
    for pat in patterns:
        for f in glob.glob(pat, recursive=True):
            if os.path.isfile(f):
                out.add(f)
            elif os.path.isdir(f):
                for root, _, names in os.walk(f):
                    out.update(os.path.join(root, n) for n in names
                               if "mail" in os.path.join(root, n).lower() and os.path.isfile(os.path.join(root, n)))
    return sorted(out)


NAME_DATE = re.compile(r"(?<!\d)(20\d\d)-?(\d\d)-?(\d\d)(?!\d)")


def file_date(path):
    """When a log file was rotated, from the date in its name (maillog-20260101.gz, maillog-2026-01-01), at the end of
    that day; else when it last changed. A copy in an archive keeps its name but often not its time."""
    m = NAME_DATE.search(os.path.basename(path))
    if m:
        try:
            return time.mktime((int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59, 59, 0, 0, -1)) \
                if 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31 else os.path.getmtime(path)
        except (OverflowError, ValueError):
            pass
    return os.path.getmtime(path)


def read_file(path, seen):
    """Every line of one log file; how far it goes: the time of its first and last line."""
    out = {"source": path, "lines": 0, "oldest": "", "newest": "", "error": ""}
    ref = file_date(path)
    first, last_line = None, ""
    try:
        with open(path, "rb") as raw:
            magic = raw.read(6)
        # logrotate's compress: gzip by default, bzip2 or xz with compresscmd
        f = (gzip.open(path, "rb") if magic[:2] == b"\x1f\x8b" else bz2.open(path, "rb") if magic[:3] == b"BZh"
             else lzma.open(path, "rb") if magic == b"\xfd7zXZ\x00" else open(path, "rb"))
        with f:
            for b in f:
                line = b.decode("utf-8", "replace")
                out["lines"] += 1
                t = seen.line(line, ref)
                if first is None:
                    first = t if t is not None else stamp(line, ref)
                if line.strip():
                    last_line = line
    except (IOError, OSError, EOFError, ValueError, lzma.LZMAError) as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    last = stamp(last_line, ref) if last_line else None
    for t in (first, last):
        seen.when(t)
    out["oldest"], out["newest"] = iso(first), iso(last)
    return out


def read_journal(module, seen, identifier):
    """Postfix's smtpd lines in the journal, and the journal's oldest line."""
    out = {"source": "journal (%s)" % identifier, "lines": 0, "oldest": "", "newest": "", "error": ""}
    jc = module.get_bin_path("journalctl")
    if not jc:
        out["error"] = "no journalctl"
        return out
    # the journal's oldest entry (its first line): how far back it goes
    p = subprocess.Popen([jc, "-o", "short-iso", "--no-pager", "-q"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        line = p.stdout.readline().decode("utf-8", "replace")
    finally:
        p.kill()
        p.wait()
    oldest = stamp(line, time.time())
    rc, text, err = module.run_command([jc, "-o", "short-iso", "--no-pager", "-q", "-t", identifier], check_rc=False)
    if rc != 0:
        out["error"] = (err or "journalctl failed").strip()[:300]
        return out
    newest = None
    for line in text.splitlines():
        out["lines"] += 1
        t = seen.line(line, time.time())
        newest = t if t is not None else newest
    seen.when(oldest)
    seen.when(newest)
    out["oldest"], out["newest"] = iso(oldest), iso(newest)
    return out


def main():
    module = AnsibleModule(argument_spec=dict(
        paths=dict(type="list", elements="str", default=["/var/log/maillog*"]),
        journal=dict(type="str", default="auto", choices=["auto", "true", "false"]),
        identifier=dict(type="str", default="postfix/smtpd"),
        max_connect_only=dict(type="int", default=500),
    ), supports_check_mode=True)
    p = module.params
    seen = Seen()
    files = log_files(p["paths"])
    sources = [read_file(f, seen) for f in files]
    if p["journal"] == "true" or (p["journal"] == "auto" and not files):
        sources.append(read_journal(module, seen, p["identifier"]))
    sent = dict((ip, c) for ip, c in seen.clients.items() if c["sent"])
    only = sorted(((ip, c) for ip, c in seen.clients.items() if not c["sent"]), key=lambda x: -x[1]["last_connect"])
    clients = dict(sent, **dict(only[:p["max_connect_only"]]))
    for c in clients.values():
        for k in ("last_sent", "first_sent", "last_connect"):
            c[k] = iso(c[k])
    module.exit_json(changed=False, clients=clients, oldest=iso(seen.oldest), newest=iso(seen.newest), sources=sources,
                     connect_only_left_out=max(len(only) - p["max_connect_only"], 0))


if __name__ == "__main__":
    main()
