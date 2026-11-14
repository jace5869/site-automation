# -*- coding: utf-8 -*-
"""The certificate expiry timeline (1.64.0): a section of the certificate reports - Linux, Windows, network devices,
vCenter (playbooks/cert_report.yml, win_cert_report.yml, net_cert_report.yml, vmware_cert_report.yml).

On the report website (the atlas and brief looks): the next cert_timeline_days (120) on one axis, a row per system (the
host's inventory group, or cert_timeline_groups), a mark at each expiry date, the bands critical / warning / Low / OK
from the report's own days, today on the left. In the email and the other looks: the renew list - what expires within
the report's notice days, soonest first, with who renews it - and the numbers. In the workbook: a Timeline sheet, every
certificate in the window. Read only: it changes no finding and no job's pass or fail.

A certificate (the reports' own rows): {host, subject, where, expires ('YYYY-MM-DD'), days, issuer, trust (a trust
store's: left out - it is not renewed on the host), group (the row it goes in; else kind - vcenter: vCenter, esxi: its
cluster - else from host_groups), renew (who renews it: a word of opts.renew_words, else opts.renew; vmca: true reads
as the word vmca)}. A certificate whose expiry date could not be read (days -99999) is left out: the report's own
table and findings have it.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import calendar
import re
import time

BANDS = ("critical", "warning", "low", "ok")
WORD = {"critical": "Critical", "warning": "Warning", "low": "Low", "ok": "OK"}
RANK = {"critical": 0, "warning": 1, "low": 2, "ok": 3}
UNREAD = -99999                                  # check_certs' mark for "the expiry date could not be read"
ROWS = 8                                         # at most this many rows; the groups with the latest expiries share Other
TIP_NAMES = 6                                    # a mark of many certificates names this many in its tip
# the label fit: a track this wide at an 800 px window (the narrow labels) and at 1280 px (the wide ones too), a
# label's width from its characters. Both on the careful side: a label too many would sit on its neighbour.
TRACK_NARROW, TRACK_WIDE, CHAR_PX, LABEL_GAP, LABEL_MAX = 420, 700, 6.8, 8, 22
# marks closer than this on the narrow track would sit on each other (a mark is 20 px, one with a number up to 28):
# they are one mark with their number, at the soonest date, and its tip has each certificate with its date
MARK_PX = 28
CN_RE = re.compile(r"(?:^|[,/])\s*CN\s*=\s*([^,/]+)", re.I)


def _int(x, d=0):
    try:
        return int(x)
    except (TypeError, ValueError):
        return d


def cert_timeline_band(days, warn=30, crit=7, notice=60):
    """critical (within crit days, or expired), warning (within warn), low (within notice: Low, 1.56.0), else ok."""
    d, warn = _int(days), _int(warn, 30)
    crit, notice = min(_int(crit, 7), warn), max(_int(notice, 60), warn)
    return "critical" if d <= crit else "warning" if d <= warn else "low" if d <= notice else "ok"


def cert_timeline_group(group_names, groups=None, other="Other"):
    """The host's row: the first of cert_timeline_groups it is in (else Other); without the setting its first
    inventory group; a host in no group: other (Other; the network report: Network devices)."""
    mine = [str(g) for g in group_names or [] if str(g) not in ("all", "ungrouped")]
    for g in groups or []:
        if str(g) in mine:
            return str(g)
    if groups:
        return "Other"
    return mine[0] if mine else str(other or "Other")


def _host_short(host):
    """A host's short name; an address stays whole."""
    host = str(host or "")
    return host if re.match(r"^[0-9.]+$|.*:", host) else host.split(".")[0]


def cert_timeline_name(subject, host=""):
    """A certificate's short name: its CN - the host's short name when the CN is the host; without a CN the host."""
    m = CN_RE.search(str(subject or ""))
    cn = m.group(1).strip() if m else ""
    short = _host_short(host)
    if not cn:
        return short or str(subject or "")[:40]
    if short and not re.match(r"^[0-9.]+$|.*:", str(host)) and (cn.lower() == str(host).lower() or cn.lower().startswith(short.lower() + ".")
                                                               or cn.lower() == short.lower()):
        return short
    return cn


def _kind_group(c):
    """A vCenter report's row: vCenter itself, an ESXi host's cluster (ESXi hosts without one)."""
    kind = str(c.get("kind") or "")
    return "vCenter" if kind == "vcenter" else (str(c.get("cluster") or "") or "ESXi hosts") if kind == "esxi" else ""


def _day(text):
    """'2026-11-19' -> its UTC midnight in seconds; None when it is no such date."""
    try:
        return calendar.timegm(time.strptime(str(text)[:10], "%Y-%m-%d"))
    except (TypeError, ValueError):
        return None


def _fmt(epoch, how="%a %d %b %Y"):
    return time.strftime(how, time.gmtime(epoch))


def _when(c):
    d = c["days"]
    return "expired %d day%s ago" % (-d, "" if d == -1 else "s") if d < 0 else "today" if d == 0 else "%d day%s" % (d, "" if d == 1 else "s")


def _short(text, n=LABEL_MAX):
    text = str(text)
    return text if len(text) <= n else text[:n - 3].rstrip(" .-") + "..."


def _labels(marks, track):
    """Which marks get their name beside them on a track that many pixels wide - the worst first, then the soonest - and
    on which side (a: above, b: below), so that no two labels touch. Marks that have their label keep it (and its
    side); returns the indexes that got one now."""
    boxes = {"a": [], "b": []}

    def box(m):
        w = len(m["label"]) * CHAR_PX
        x = m["at"] * track / 100.0
        lo = min(max(x - w / 2.0, 0), max(track - w, 0))
        return lo, lo + w

    for m in marks:
        if m.get("side"):
            boxes[m["side"]].append(box(m))
    new = []
    order = sorted(range(len(marks)), key=lambda i: (RANK[marks[i]["status"]], marks[i]["at"]))
    for n, i in enumerate(order):
        m = marks[i]
        if m.get("side"):
            continue
        lo, hi = box(m)
        for side in (("b", "a") if n % 2 == 0 else ("a", "b")):
            if all(hi + LABEL_GAP <= b[0] or lo >= b[1] + LABEL_GAP for b in boxes[side]):
                boxes[side].append((lo, hi))
                m["side"] = side
                new.append(i)
                break
    return new


def _tip(day, cs):
    """A mark's hover lines: one certificate - its name, host, issuer, expiry and who renews it; many - the day (or the
    days, each certificate with its own) and the names."""
    c = cs[0]
    when = "%s (%s)" % (_fmt(day) if day is not None else c["expires"], _when(c)) if c["days"] >= 0 else \
        "%s (%s)" % (c["expires"], _when(c))
    if len(cs) == 1:
        return "\n".join([c["name"], "Host: %s" % c["host"], "Issuer: %s" % (c["issuer"] or "not read"),
                          "Expires: %s" % when, "Renewed by: %s" % c["renew"]])
    last = cs[-1]
    if max(last["days"], 0) != max(c["days"], 0):    # several days in one mark (apart, they would sit on each other)
        def date(x, how="%d %b"):
            return _fmt(_day(x["expires"]), how).lstrip("0") if _day(x["expires"]) is not None else x["expires"]
        head = "%d certificates: %s %s" % (len(cs), "expired, and up to" if c["days"] < 0 else date(c) + " to", date(last, "%a %d %b %Y"))
        lines = [head] + ["%s on %s, %s - %s" % (x["name"], x["host"], _when(x) if x["days"] <= 0 else "%s (%s)" % (date(x), _when(x)),
                                                 x["renew"]) for x in cs[:TIP_NAMES]]
    else:
        head = ("%d certificates expired" % len(cs)) if c["days"] < 0 else "%d certificates expire %s" % (len(cs), when)
        lines = [head] + ["%s on %s%s - %s" % (x["name"], x["host"], (", %s" % _when(x)) if x["days"] < 0 else "", x["renew"])
                          for x in cs[:TIP_NAMES]]
    if len(cs) > TIP_NAMES:
        lines.append("and %d more: the renew list below" % (len(cs) - TIP_NAMES))
    return "\n".join(lines)


def cert_timeline(certs, opts=None):
    """certs (see the top) -> {sections (the email's: the renew list), web_sections (the timeline, then the renew list),
    boxes (the numbers), sheet (the workbook's Timeline sheet), lines (the job output), counts, status}.
    opts: days (cert_timeline_days, 120), warn / crit / notice (the report's own days), warn_name / crit_name /
    notice_name (those settings' names, for the text), groups (cert_timeline_groups), host_groups {host: its inventory
    groups}, other (the row of a host in no group: Other), renew (who renews a certificate that names nobody: the renew
    job's name), renew_words ({a certificate's renew word: what the list says}), today ('YYYY-MM-DD', the day of the run:
    the dates under the axis), what ('Linux', 'Windows', ... in the titles)."""
    o = opts or {}
    span = max(_int(o.get("days"), 120), 14)
    warn = _int(o.get("warn"), 30)
    crit, notice = min(_int(o.get("crit"), 7), warn), max(_int(o.get("notice"), 60), warn)
    groups = [str(g) for g in o.get("groups") or []]
    host_groups = o.get("host_groups") or {}
    renew, renew_words = str(o.get("renew") or "by hand"), o.get("renew_words") or {}
    today = _day(o.get("today")) or _day(time.strftime("%Y-%m-%d", time.gmtime()))

    seen, cs, trust, unread = set(), [], 0, 0
    for c in certs or []:
        d = _int(c.get("days"), UNREAD)
        if d <= UNREAD:
            unread += 1
            continue
        if c.get("trust"):
            trust += d <= span
            continue
        host = str(c.get("host") or "")
        key = (host.lower(), str(c.get("sha256") or c.get("serial") or c.get("subject") or ""), str(c.get("expires") or ""))
        if key in seen:                              # one certificate found twice on a host (its file and its port)
            continue
        seen.add(key)
        cs.append({"host": host, "name": cert_timeline_name(c.get("subject"), host), "subject": str(c.get("subject") or ""),
                   "where": str(c.get("where") or ""), "expires": str(c.get("expires") or ""), "days": d,
                   "issuer": str(c.get("issuer") or ""), "band": cert_timeline_band(d, warn, crit, notice),
                   "group": str(c.get("group") or "") or _kind_group(c) or cert_timeline_group(host_groups.get(host), groups, o.get("other")),
                   "renew": str(renew_words.get(str(c.get("renew") or ("vmca" if c.get("vmca") else ""))) or renew)})
    cs.sort(key=lambda c: (c["days"], c["group"].lower(), c["host"].lower(), c["name"].lower()))
    inside = [c for c in cs if c["days"] <= span]
    n = dict((b, len([c for c in inside if c["band"] == b])) for b in BANDS)
    # the renew list, its boxes and the status: all that is not OK - with cert_timeline_days below the report's own
    # days, some of it expires after the window (m: its numbers; n: the window's, for the timeline)
    todo = [c for c in cs if c["band"] != "ok"]
    later = len([c for c in todo if c["days"] > span])
    m = dict((b, len([c for c in todo if c["band"] == b])) for b in BANDS)
    after = next((c for c in cs if c["band"] == "ok"), None)
    status = "critical" if m["critical"] else "warning" if m["warning"] else "ok"

    def setting(key, default):
        return ("%d days (%s)" % (default, o[key])) if o.get(key) else "%d days" % default

    # ---- the rows: the configured groups in their order, else the soonest expiry first; over ROWS, the last share Other
    names = []
    for c in inside:
        if c["group"] not in names:
            names.append(c["group"])
    if groups:
        names.sort(key=lambda g: (groups.index(g) if g in groups else len(groups), g == "Other"))
    if len(names) > ROWS:
        keep = names[:ROWS - 1]
        for c in cs:
            if c["group"] not in keep:
                c["group"] = "Other"
        names = [g for g in keep if g != "Other"] + ["Other"]
    rows = []
    for g in names:
        mine = [c for c in inside if c["group"] == g]
        by_day = {}
        for c in mine:
            by_day.setdefault(max(c["days"], 0), []).append(c)
        marks, near = [], []                          # near: [the first day, its certificates and those of the days beside it]
        for d in sorted(by_day):
            if near and (d - near[-1][0]) * TRACK_NARROW / float(span) < MARK_PX:
                near[-1][1].extend(by_day[d])
            else:
                near.append([d, list(by_day[d])])
        for d, b in near:
            day = _day(b[0]["expires"]) if b[0]["days"] >= 0 else None
            label = _short(b[0]["name"]) if len(b) == 1 else "%s +%d" % (_short(b[0]["name"], LABEL_MAX - 4), len(b) - 1)
            marks.append({"at": round(100.0 * d / span, 2), "status": min((c["band"] for c in b), key=lambda x: RANK[x]),
                          "n": len(b), "label": label, "tip": _tip(day, b), "side": "", "show": ""})
        for i in _labels(marks, TRACK_NARROW):
            marks[i]["show"] = "n"
        for i in _labels(marks, TRACK_WIDE):
            marks[i]["show"] = "w"
        worst = min((c["band"] for c in mine), key=lambda b: RANK[b])
        rows.append({"name": g, "sub": "%d certificate%s" % (len(mine), "" if len(mine) == 1 else "s"), "status": worst,
                     "word": WORD[worst], "marks": marks})

    edges = [("critical", 0, min(crit, span)), ("warning", min(crit, span), min(warn, span)),
             ("low", min(warn, span), min(notice, span)), ("ok", min(notice, span), span)]
    said = {"critical": "%d day%s or less, or expired" % (crit, "" if crit == 1 else "s"),
            "warning": "%d to %d days" % (crit + 1, warn), "low": "%d to %d days" % (warn + 1, notice),
            "ok": "more than %d days" % notice}
    bands = [{"status": b, "word": WORD[b], "from": round(100.0 * lo / span, 2), "to": round(100.0 * hi / span, 2),
              "text": said[b], "count": n[b]} for b, lo, hi in edges if hi > lo]
    step = 14 if span <= 180 else 28 if span <= 400 else 91
    ticks = [{"at": round(100.0 * d / span, 2), "text": "Today" if d == 0 else _fmt(today + d * 86400, "%d %b").lstrip("0"),
              "wide": (d // step) % 2 == 1} for d in range(0, span + 1, step) if d == 0 or 100.0 * d / span <= 97]
    what = (str(o.get("what")) + " ") if o.get("what") else ""
    line = {"title": "Certificates: the next %d days" % span, "top": True, "status": status, "count": len(inside),
            "text": "Every %scertificate that expires within %d days (cert_timeline_days), a row per system. "
                    "A mark is one certificate at its expiry date. A number in a mark: that many close together, at the first one's "
                    "date; its tip has each with its date." % (what, span),
            "timeline": {"days": span, "label": "%sertificates by expiry date, the next %d days" % ((what + "c") if what else "C", span),
                         "week": round(100.0 * 7 / span, 4), "bands": bands, "ticks": ticks, "groups": rows,
                         "none": "" if rows else "No certificate expires within %d days." % span}}

    # ---- the renew list (the email, every look): what is not OK, soonest first
    note = []
    if n["ok"]:
        note.append("%d more expire%s within %d days and %s OK: the workbook's Timeline sheet has %s." % (
            n["ok"], "s" if n["ok"] == 1 else "", span, "is" if n["ok"] == 1 else "are", "it" if n["ok"] == 1 else "them"))
    if later:
        note.append("%d of the list expire%s after the timeline's %d days (cert_timeline_days)." % (later, "s" if later == 1 else "", span))
    if after:
        note.append("Next after the list: %s, in %d days." % (
            after["host"] if after["name"] == _host_short(after["host"]) else "%s on %s" % (after["name"], after["host"]), after["days"]))
    if trust:
        note.append("Not listed: %d trust store certificate%s (not renewed on the host)." % (trust, "" if trust == 1 else "s"))
    head = "Critical: %d day%s or less, or expired%s. Warning: up to %s. Low: up to %s, nothing to do now." % (
        crit, "" if crit == 1 else "s", (" (%s)" % o["crit_name"]) if o.get("crit_name") else "", setting("warn_name", warn),
        setting("notice_name", notice))
    renew_list = {"title": "Certificates to renew, soonest first", "top": True,
                  "status": status, "text": " ".join([head] + note),
                  "columns": ["Level", "Certificate", "System", "Host", "Expires", "Days left", "Renewed by"],
                  "rows": [[WORD[c["band"]], c["name"], c["group"], c["host"],
                            ("%s %s" % (c["expires"], _fmt(_day(c["expires"]), "%a"))) if _day(c["expires"]) is not None else c["expires"],
                            c["days"], c["renew"]] for c in todo],
                  "row_status": [c["band"] for c in todo],
                  "cell_status": [[c["band"], "", "", "", "", c["band"], ""] for c in todo]}
    if not todo:
        renew_list = {"title": "Certificates to renew, soonest first", "top": True, "status": "ok",
                      "lines": ["Nothing to renew: no certificate expires within %d days." % notice] + note}
    names3 = ", ".join("%s (%s)" % (c["name"], _when(c)) for c in todo[:3])

    def box(label, value, st, text):
        return {"label": label, "value": value, "status": st if value else "ok", "note": text}

    boxes = [box("Expire in %d days" % crit, m["critical"], "critical",
                 ("the first %s%s" % ("" if todo[0]["days"] < 0 else "expires " if todo[0]["days"] == 0 else "in ", _when(todo[0])))
                 if m["critical"] else "or expired: none"),
             box("Expire in %d days" % warn, m["critical"] + m["warning"], "warning", "critical and warning"),
             box("Low: %d to %d days" % (warn + 1, notice), m["low"], "low", "worth knowing")]
    sheet = {"name": "Timeline",
             "columns": ["Days left", "Expires", "Band", "Certificate", "System", "Host", "Where", "Issuer", "Renewed by", "Subject"],
             "rows": [[c["days"], c["expires"], WORD[c["band"]], c["name"], c["group"], c["host"], c["where"], c["issuer"],
                       c["renew"], c["subject"]] for c in inside + [c for c in todo if c["days"] > span]]}
    renew_list["boxes"] = boxes
    out = ["Certificate timeline, the next %d days: %d certificate(s) - %d critical, %d warning, %d Low, %d OK%s" % (
        span, len(inside), n["critical"], n["warning"], n["low"], n["ok"], ("; soonest: " + names3) if names3 else "")]
    if later:
        out.append("  After the %d days, in the renew list: %d certificate(s)" % (span, later))
    for r in rows:
        out.append("  %s: %s, %s" % (r["name"], r["sub"], r["word"]))
    counts = dict(n, window=len(inside), days=span, certificates=len(cs), trust=trust, unread=unread, later=later)
    return {"sections": [renew_list], "web_sections": [line, renew_list], "boxes": boxes, "sheet": sheet, "lines": out,
            "counts": counts, "status": status}


def cert_timeline_doc(doc, timeline, web=False, boxes=True):
    """A report document (network devices, vCenter: made by their own filter) with the timeline on top: the renew list
    (web: the timeline section first) above its sections, the numbers after its summary boxes. An empty timeline
    (cert_timeline: false): the document as it is."""
    if not timeline or not doc:
        return doc
    out = dict(doc)
    out["sections"] = list(timeline["web_sections" if web else "sections"]) + list(doc.get("sections") or [])
    if boxes:
        out["summary"] = list(doc.get("summary") or []) + list(timeline.get("boxes") or [])
    return out


class FilterModule(object):
    def filters(self):
        return {"cert_timeline": cert_timeline, "cert_timeline_doc": cert_timeline_doc, "cert_timeline_band": cert_timeline_band,
                "cert_timeline_group": cert_timeline_group, "cert_timeline_name": cert_timeline_name}
