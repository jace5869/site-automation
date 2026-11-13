# -*- coding: utf-8 -*-
"""Ticket insights (roles/servicenow_insights, docs/SERVICENOW_INSIGHTS.md): what the report knows, worked out from the
records site_servicenow_insights read - the issues, keeping up, severity, problem candidates, hand-offs, requests,
changes, close notes, All-stars, knowledge drafts - for the whole site and for each team (the model), and the pieces
around it: the masking before ACT (GenAI) and the questions ACT is asked, in batches, with its answers read back; the
email's report (site_email's _report), the workbook and the job artifact. The web page is servicenow_insights_page.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import bisect
import datetime
import hashlib
import functools
import fnmatch
import json
import re

try:
    from zoneinfo import ZoneInfo
except ImportError:            # Python 3.8: UTC only
    ZoneInfo = None

UTC = datetime.timezone.utc
MARK_BEGIN = "BEGIN_ACT_ANALYSIS"
MARK_END = "END_ACT_ANALYSIS"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# a close note that says what was done names an action
ACTION = re.compile(
    r"\b(restart|reboot|reset|replac|reinstall|install|uninstall|unlock|clear|delet|remov|add|updat|patch|upgrad|configur|"
    r"reconfigur|chang|enabl|disabl|re-?enabl|map|grant|recreat|re-?creat|rebuil|renew|roll ?back|revert|increas|extend|expand|"
    r"mov|migrat|re-?join|join|creat|fix|correct|repair|adjust|set|assign|provision|issu|restor|recover|rotat|resiz|free|purg|flush|"
    r"kill|terminat|power|reseat|swap|re-?imag|appl|ran|run|execut|edit|modif|clean|walk|show|explain|import|export|renam|"
    r"reassign|refresh|sync|resync|re-?sync|rebind|re-?register|register|reload|repoint|point|stop|start|activat|deactivat|"
    r"unblock|block|whitelist|allow|approv|order|deploy|redeploy|copy|copied|restart)\w*", re.I)
TICKET = re.compile(r"^(?:INC|RITM|REQ|SCTASK|TASK|CHG|CTASK|PRB|PTASK|KB)\d+$", re.I)
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]\d)")
PHONE = re.compile(r"(?<![\w-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\w-])|(?<![\w-])\d{3}-\d{4}(?![\w-])")
# each ends before a space, a sign, or a full stop that ends the sentence ("web01." and "web01.corp" differ)
ID10 = re.compile(r"(?<![\w.-])\d{10}(?![\w-]|\.\w)")
FQDN = re.compile(r"(?<![\w.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:mil|gov|com|net|org|edu|local|lan|internal|corp|us)(?![\w-]|\.\w)", re.I)
HOSTLIKE = re.compile(r"(?<![\w.-])[A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*\d[A-Za-z0-9_-]*(?![\w-]|\.\w)")
PLACEHOLDER = re.compile(r"\b(PERSON|PHONE|EMAIL|HOST|IP|ID)-(\d+)\b")
# tokens with a digit that are not host names
KEEP_WORDS = set("""win7 win8 win10 win11 windows7 windows10 windows11 server2016 server2019 server2022 rhel6 rhel7 rhel8 rhel9 rhel10
centos7 ubuntu20 ubuntu22 ubuntu24 o365 m365 office365 365 2fa mfa x86 x64 x86_64 amd64 arm64 ipv4 ipv6 tls1 tls12 tls13 sha1
sha256 sha384 sha512 rsa2048 rsa4096 utf8 utf-8 base64 http2 h264 4k 1080p 1st 2nd 3rd 4th 5th 24x7 24/7 b2b p1 p2 p3 p4 p5 tier1
tier2 tier3 l1 l2 l3 q1 q2 q3 q4 fy24 fy25 fy26 fy27 cac2 piv2 dod1 vpn1 802.1x wpa2 wpa3 usb2 usb3 usb-c hdmi1 hdmi2 dp1 dp2 ad1
cve windows-11 windows-10 sp1 sp2 v1 v2 v3 v4 v5 v6 v7 v8 v9 1h 2h 4h 8h 24h 48h 72h""".split())
COMMON_WORDS = set("""the and for with from that this have will your user users team group ticket issue error print printer drive account
password reset locked unlock email outlook teams office service server desktop laptop network access share folder file files
please thanks thank help need needs request requests new open close closed resolved fixed done working work works call called
phone mobile device devices card reader smart smartcard certificate certificates update updated install installed software
mail time date name names site building room floor desk monday tuesday wednesday thursday friday saturday sunday morning
afternoon evening today tomorrow yesterday week month year back down long short high low good bad fine okay will still also
young brown white black green gray grey king bishop baker cook hunter carter mason porter smith miller page""".split())
# what may follow a last name ("Mary Ann Jones Jr.")
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
REQUEST_WORDS = re.compile(r"\b(install|new account|create (an )?account|access to|request(ing)? access|need(s)? access|add (me|user)|"
                           r"order|purchase|new (laptop|phone|monitor|keyboard|mouse|headset)|software request|license)\b", re.I)


# ---- small helpers ---------------------------------------------------------------------------------------------

def _t(text):
    """ServiceNow's 'YYYY-MM-DD HH:MM:SS' (UTC) -> aware datetime, or None."""
    return _t_cached(str(text or "").strip())


@functools.lru_cache(maxsize=200000)
def _t_cached(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _tz(name):
    n = str(name or "").strip()
    if not n or n.upper() == "UTC" or ZoneInfo is None:
        return UTC
    try:
        return ZoneInfo(n)
    except Exception:            # noqa: BLE001
        return UTC


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _pct(a, b):
    return (100.0 * a / b) if b else None


def _hours(it):
    o, d = _t(it.get("opened_at")), _t(it.get("done_at"))
    if o and d and d >= o:
        return (d - o).total_seconds() / 3600.0
    return None


# ---- met or missed (1.7.0): task_sla's Response and Resolution, else your targets (business hours), else Made SLA ----

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
NO_TARGET = object()
# the standard 3 x 3 ITIL matrix: impact -> urgency -> priority (1 Critical ... 5)
PRIORITY_MATRIX = {"1": {"1": "1", "2": "2", "3": "3"}, "2": {"1": "2", "2": "3", "3": "4"}, "3": {"1": "3", "2": "4", "3": "5"}}
PRIORITY_NAMES = {"1": "Critical", "2": "High", "3": "Moderate", "4": "Low", "5": "Exempt"}
# a close code (as shown, any case) -> how the incident ended; any other code: Fixed
CORE_HOURS = {"days": ["mon", "tue", "wed", "thu", "fri"], "start": "07:30", "end": "16:30"}
STATE_KINDS = ("new", "progress", "hold", "resolved")
OUTCOME_MAP = {"Workaround": "Workaround", "Workaround provided": "Workaround", "Duplicate": "Duplicate", "Known Error": "Known Error",
               "No Resolution Provided": "No Resolution Provided"}


def _hm(text, what):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 24 or int(m.group(2)) > 59:
        raise ValueError("servicenow_insights_business_hours %s must be HH:MM, not %s" % (what, text))
    return int(m.group(1)) * 60 + int(m.group(2))


def business_hours_spec(bh):
    """servicenow_insights_business_hours -> (weekdays 0-6, start minute, end minute, holidays {YYYY-MM-DD}), or None
    ({} = calendar hours). An end at or before the start is a shift that runs past midnight (it belongs to its start day)."""
    if not bh:
        return None
    if not isinstance(bh, dict):
        raise ValueError("servicenow_insights_business_hours must be a mapping, e.g. {days: [mon, tue, wed, thu, fri], start: '07:30', end: '16:30'}")
    days = bh.get("days") or ["mon", "tue", "wed", "thu", "fri"]
    if isinstance(days, str):
        days = days.replace(",", " ").split()
    wd = set()
    for d in days:
        k = str(d).strip().lower()[:3]
        if k not in WEEKDAYS:
            raise ValueError("servicenow_insights_business_hours days: %s is not a day (mon ... sun)" % d)
        wd.add(WEEKDAYS.index(k))
    hol = set()
    for h in bh.get("holidays") or []:
        t = str(h).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            raise ValueError("servicenow_insights_business_hours holidays: %s is not a date (YYYY-MM-DD)" % h)
        hol.add(t)
    return wd, _hm(bh.get("start", "00:00"), "start"), _hm(bh.get("end", "24:00"), "end"), hol


def state_kind(name):
    """A state as shown -> new (waiting to start), progress, hold, resolved - or '' (closed, cancelled: not counted)."""
    t = str(name or "").lower()
    if "hold" in t or "await" in t or "pending" in t:
        return "hold"
    if "resolv" in t:
        return "resolved"
    if t.strip() == "new" or t.startswith("new") or "open" == t.strip():
        return "new"
    if "progress" in t or "assigned" in t or "work" in t:
        return "progress"
    return ""


def in_hours(t, spec, tz):
    """t (aware) inside the hours of spec (business_hours_spec), in tz."""
    if t is None or spec is None:
        return True
    days, sm, em, hol = spec
    lt = t.astimezone(tz)
    mins = lt.hour * 60 + lt.minute
    if em > sm:
        return lt.weekday() in days and lt.date().isoformat() not in hol and sm <= mins < em
    prev_d = lt.date() - datetime.timedelta(days=1)                     # a shift past midnight belongs to its start day
    return (lt.weekday() in days and lt.date().isoformat() not in hol and mins >= sm) or (
        prev_d.weekday() in days and prev_d.isoformat() not in hol and mins < em)


def business_hours(a, b, spec, tz):
    """Hours from a to b (aware datetimes) inside the business hours (spec: business_hours_spec), in the time zone tz;
    calendar hours when spec is None."""
    if a is None or b is None or b < a:
        return None
    if spec is None:
        return (b - a).total_seconds() / 3600.0
    days, sm, em, hol = spec
    total = 0.0
    d = a.astimezone(tz).date() - datetime.timedelta(days=1)           # yesterday's overnight shift may run into a
    last = b.astimezone(tz).date()
    while d <= last:
        if d.weekday() in days and d.isoformat() not in hol:
            ws = datetime.datetime(d.year, d.month, d.day, tzinfo=tz) + datetime.timedelta(minutes=sm)
            end_day = d + datetime.timedelta(days=1) if em <= sm else d
            we = datetime.datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz) + datetime.timedelta(minutes=em)
            lo, hi = max(a, ws), min(b, we)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600.0
        d += datetime.timedelta(days=1)
    return total


def _target_entry(it, o):
    """The ticket's entry in servicenow_insights_sla_targets (by table, then priority, "*" for the rest; a catalog item's
    own in servicenow_insights_sla_items): a number (resolution hours), {response, resolution}, None (no SLA: none set on
    purpose) - or NO_TARGET (the table or priority not there: ServiceNow's Made SLA counts)."""
    items = (o or {}).get("sla_items") or {}
    if it.get("table") == "sc_req_item" and it.get("cat_item") in items:
        return items[it["cat_item"]]
    by = ((o or {}).get("sla_targets") or {}).get(it.get("table"))
    if not by:
        return NO_TARGET
    by = dict((str(k).strip().upper().lstrip("P"), v) for k, v in by.items())
    return by.get(str(it.get("priority_value") or "").strip(), by.get("*", NO_TARGET))


def _none(v):
    return v is None or str(v).strip().lower() in ("", "none", "null", "~")


def sla_target(it, o):
    """Your resolution target for a ticket, in hours - or None (none set: ServiceNow's Made SLA counts, or no SLA)."""
    v = _target_entry(it, o)
    if v is NO_TARGET or _none(v):
        return None
    v = v.get("resolution") if isinstance(v, dict) else v
    return None if _none(v) else float(v)


def response_target(it, o):
    """Your response target for a ticket, in hours (servicenow_insights_sla_targets {..: {response: 0.5, ...}}) - or None."""
    v = _target_entry(it, o)
    if not isinstance(v, dict) or _none(v.get("response")):
        return None
    return float(v["response"])


def sla_kind(name, o=None):
    """An SLA definition's kind by its name: response, resolution (servicenow_insights_sla_*_match, any case), else other."""
    t = str(name or "").lower()
    if any(str(w).lower() in t for w in (o or {}).get("sla_response_match") or ["response"] if str(w).strip()):
        return "response"
    if any(str(w).lower() in t for w in (o or {}).get("sla_resolution_match") or ["resolution", "resolve"] if str(w).strip()):
        return "resolution"
    return "other"


def apply_slas(items, r, o, tz=UTC):
    """Each incident's and requested item's met or missed, done tickets only: _resp (Response), _resol (Resolution; made_sla
    follows it, so every figure that read Made SLA reads it), _no_slo (no SLA at all, out of the percentages), _slas.
    task_sla: an SLA of the kind breached = missed; the targets: within the hours (business hours when set) - the response
    from opened to first assigned to a person; made_sla: ServiceNow's flag. -> the source used."""
    sla = (r or {}).get("sla") or {}
    src = sla.get("source") or "targets"           # a reading from before 1.7.0: the targets where set, else Made SLA
    spec = business_hours_spec((o or {}).get("business_hours"))
    recs = sla.get("records") or {}
    first = (r or {}).get("first_assigned") or {}
    for it in items:
        it["_resp"] = it["_resol"] = None
        it["_no_slo"], it["_slas"], it["_sla_recs"] = False, [], []
        if it.get("table") not in ("incident", "sc_req_item"):
            continue
        it["sn_made_sla"] = it.get("made_sla")
        done = _t(it.get("done_at"))
        if src == "task_sla":
            mine = recs.get(it.get("sys_id")) or []
            it["made_sla"] = None
            if not done:
                continue
            if not mine:
                it["_no_slo"] = True
                continue
            by = {}
            for x in mine:
                k = sla_kind(x.get("name"), o)
                by.setdefault(k, []).append(x)
                it["_slas"].append([x.get("name") or "", k])
                # 1.43.0: each SLA's own outcome and elapsed hours (the VIP section's SLA definitions)
                el = x.get("elapsed_s")
                it["_sla_recs"].append([x.get("name") or "", k, bool(x.get("breached")), None if el is None else el / 3600.0])
            it["_resp"] = (not any(x.get("breached") for x in by["response"])) if by.get("response") else None
            it["_resol"] = (not any(x.get("breached") for x in by["resolution"])) if by.get("resolution") else None
            it["made_sla"] = it["_resol"]
            continue
        if src == "targets":
            entry = _target_entry(it, o)
            if entry is not NO_TARGET and (_none(entry) or (isinstance(entry, dict) and _none(entry.get("resolution")) and _none(entry.get("response")))):
                it["made_sla"], it["_no_slo"] = None, bool(done)
                continue
            t = sla_target(it, o)
            if t is not None:
                h = business_hours(_t(it.get("opened_at")), done, spec, tz)
                it["made_sla"] = None if h is None else h <= t
                it["sla_target_h"] = t
            rt = response_target(it, o)
            if rt is not None and done and it.get("table") == "incident" and first.get(it.get("sys_id")):
                h = business_hours(_t(it.get("opened_at")), _t(first[it["sys_id"]]), spec, tz)
                it["_resp"] = None if h is None else h <= rt
        it["_resol"] = it.get("made_sla") if done else None
    return src


def apply_sla_targets(items, o):
    """Made SLA from your targets (calendar hours), for the callers before 1.7.0: apply_slas with the targets."""
    apply_slas(items, {"sla": {"source": "targets"}}, o)


def priority_label(value, shown="", o=None):
    """'1 - Critical': the priority's number and its name (servicenow_insights_priority_names), else as ServiceNow shows it."""
    names = dict((str(k).strip().upper().lstrip("P"), str(v)) for k, v in ((o or {}).get("priority_names") or PRIORITY_NAMES).items())
    v = str(value or "").strip()
    return ("%s - %s" % (v, names[v])) if v in names else (shown or "(none)")


def matrix_priority(it, o=None):
    """The priority the matrix gives the incident's impact x urgency (servicenow_insights_priority_matrix; [] or {} = off),
    or '' (off, or impact / urgency not read)."""
    m = (o or {}).get("priority_matrix", PRIORITY_MATRIX)
    if not m or not isinstance(m, dict):
        return ""
    row = dict((str(k), v) for k, v in m.items()).get(str(it.get("impact_value") or ""))
    if not isinstance(row, dict):
        return ""
    return str(dict((str(k), v) for k, v in row.items()).get(str(it.get("urgency_value") or ""), "") or "")


def category_name(it):
    return " ".join(str(it.get("category") or "").split()) or "(none)"


def _norm(text):
    """A text for comparing: lower case, words only."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _o(opts, key, default):
    v = (opts or {}).get(key)
    return default if v is None or v == "" else v


def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80].rstrip("-") or "all"


def short_names(names):
    """Each group's short name for the paths and the bars: the shortest run of its parts (the name split at its dashes)
    that no other group's name holds - so 'Ops - Linux - Contractor' among 'Ops - Windows - Contractor' and
    'Network - Contractor' is 'Linux', and a site prefix or a contract suffix every group carries never shows. Of two
    runs as short, the one nearest the end (the most specific part of a name). A name nothing distinguishes stays whole.
    -> {name: short}."""
    names = sorted(set(str(n) for n in names or [] if str(n or "").strip()))
    parts = dict((n, [p for p in re.split(r"\s+-\s*|\s*-\s+", n) if p] or [n]) for n in names)
    low = dict((n, [p.lower() for p in parts[n]]) for n in names)

    def holds(seq, sub):
        return any(seq[i:i + len(sub)] == sub for i in range(len(seq) - len(sub) + 1))

    out = {}
    for n in names:
        ps, lp = parts[n], low[n]
        out[n] = n
        for size in range(1, len(ps) + 1):
            starts = [i for i in range(len(ps) - size + 1) if not any(holds(low[o], lp[i:i + size]) for o in names if o != n)]
            if starts:
                out[n] = " - ".join(ps[starts[-1]:starts[-1] + size])
                break
    return out


def name_list(value):
    """A list, or text with names one per line or separated by ; or , -> the names (never cut at spaces or dashes)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[;,\n]", value) if x.strip()]
    if isinstance(value, dict):
        raise ValueError("a list of names, not a mapping: %r" % (value,))
    return [str(x).strip() for x in value if str(x).strip()]


# ---- close notes ---------------------------------------------------------------------------------------------------

def grade(note, opts=None):
    """A close note -> good (says what was done), thin (longer, but no action) or bare (short, or a stock phrase)."""
    o = opts or {}
    raw = " ".join(str(note or "").split())
    n = _norm(raw)
    phrases = set(_norm(p) for p in (o.get("bare_phrases") or []))
    if not n or len(raw) < int(_o(o, "bare_chars", 20)) or n in phrases:
        return "bare"
    if ACTION.search(raw) and len(raw) >= 40:
        return "good"
    return "thin"


# ---- masking: what never reaches the model -------------------------------------------------------------------

def _people(items):
    out = set()
    for it in items:
        for k in ("assigned_to", "opened_by", "caller", "resolved_by", "closed_by"):
            n = str(it.get(k) or "").strip()
            if n and n.lower() not in ("(unassigned)", "system", "guest", "admin"):
                out.add(n)
    return out


def _scan(text, found, keep):
    """What a text holds that is masked: email and IP addresses, phone numbers, 10-digit IDs, host names -> found."""
    if not text:
        return
    for m in EMAIL.findall(text):
        found.setdefault(m, "EMAIL")
    for m in IPV4.findall(text):
        found.setdefault(m, "IP")
    for m in PHONE.findall(text):
        found.setdefault(m.strip(), "PHONE")
    for m in ID10.findall(text):
        found.setdefault(m, "ID")
    stripped = EMAIL.sub(" ", text)
    for m in FQDN.findall(stripped):
        if not IPV4.fullmatch(m):
            found.setdefault(m, "HOST")
    for m in HOSTLIKE.findall(stripped):
        if TICKET.match(m) or m.lower() in keep or IPV4.fullmatch(m) or re.fullmatch(r"[\d-]+", m):
            continue
        if re.fullmatch(r"[A-Za-z]+\d{1,2}", m) and len(m) <= 4:      # v2, p1, l3 ...
            continue
        found.setdefault(m, "HOST")


def servicenow_insights_mask(r, opts=None):
    """The replacements made before ACT reads anything: the people the records name (full name, "Last, First", the last
    name), host-like configuration items, and what the texts hold - email addresses, IP addresses, phone numbers, 10-digit
    IDs, host names - each real value -> a placeholder (PERSON-1, HOST-3 ...), the same everywhere. -> {pairs: [[real,
    placeholder]] longest first, counts: {kind: n}}."""
    o = opts or {}
    items = (r or {}).get("items") or []
    keep = KEEP_WORDS | set(str(w).strip().lower() for w in (o.get("mask_keep") or []) if str(w).strip())
    found, exact = {}, set()   # real -> kind; the last names matched only as written (capitalised)
    for p in _people(items):
        found.setdefault(p, "PERSON")
        # "Last, First" or "First [Middle] Last [Jr.]"
        parts = [x for x in p.split(",")[0].split() if x.rstrip(".").lower() not in NAME_SUFFIXES]
        last = (" ".join(parts) if "," in p else parts[-1]) if len(parts) >= (1 if "," in p else 2) else ""
        if "," not in p and last:
            found.setdefault("%s, %s" % (last, parts[0]), "PERSON")
        # a last name the people fields give is always masked; a short or word-like one (Do, Page) only as a name
        if len(last) >= 2:
            if len(last) <= 3 or last.lower() in COMMON_WORDS:
                last = last if last[:1].isupper() else last.capitalize()
                exact.add(last)
            found.setdefault(last, "PERSON")
    for w in o.get("mask_names") or []:
        if str(w).strip():
            found.setdefault(str(w).strip(), "PERSON")
    for it in items:
        ci = str(it.get("cmdb_ci") or "").strip()
        if ci and " " not in ci and (re.search(r"\d", ci) or "." in ci or "-" in ci) and ci.lower() not in keep:
            found.setdefault(ci, "HOST")
    for it in items:
        if it.get("table") not in ("incident", "sc_task", "sc_req_item"):
            continue
        for field in ("short_description", "close_notes"):
            _scan(str(it.get(field) or ""), found, keep)
    counters, pairs, counts = {}, [], {}
    for real in sorted(found, key=lambda s: (found[s], s.lower())):
        kind = found[real]
        counters[kind] = counters.get(kind, 0) + 1
        pairs.append([real, "%s-%d" % (kind, counters[kind])])
        counts[kind] = counters[kind]
    pairs.sort(key=lambda p: -len(p[0]))
    return dict({"pairs": pairs, "counts": counts}, **({"exact": sorted(exact)} if exact else {}))


_MASK_CACHE = []


def servicenow_insights_mask_more(mask, work_notes, opts=None):
    """The mask with what the work notes hold besides (0.96.0): their email and IP addresses, phone numbers, IDs and host
    names get placeholders too, numbered on from the mask's own (the earlier ones stay as they were). work_notes: the
    module's answer ({work_notes: {sys_id: [[when, text]]}}) or that dict. Kept apart from the mask the issues are grouped
    with: the words of what only the work notes hold would change the descriptions' keys (desc_key)."""
    o = opts or {}
    wn = (work_notes or {}).get("work_notes", work_notes) if isinstance(work_notes, dict) else {}
    keep = KEEP_WORDS | set(str(w).strip().lower() for w in (o.get("mask_keep") or []) if str(w).strip())
    have = set(p[0].lower() for p in (mask or {}).get("pairs") or [])
    found = {}
    for notes in (wn or {}).values():
        for x in notes or []:
            _scan(str(x[1] if isinstance(x, (list, tuple)) and len(x) > 1 else x or ""), found, keep)
    counts = dict((mask or {}).get("counts") or {})
    pairs = [list(p) for p in (mask or {}).get("pairs") or []]
    for real in sorted(found, key=lambda v: (found[v], v.lower())):
        if real.lower() in have:
            continue
        have.add(real.lower())
        kind = found[real]
        counts[kind] = counts.get(kind, 0) + 1
        pairs.append([real, "%s-%d" % (kind, counts[kind])])
    pairs.sort(key=lambda p: -len(p[0]))
    return dict({"pairs": pairs, "counts": counts}, **({"exact": mask["exact"]} if (mask or {}).get("exact") else {}))


def _mask_parts(mask):
    """(pattern of every real value, real -> placeholder, placeholder -> real, words of the real values) - kept for the
    last few masks seen (never stored on the mask itself: it is a fact)."""
    pairs = (mask or {}).get("pairs") or []
    for hit in _MASK_CACHE:
        if hit[0] is pairs:
            return hit[1:]
    exact = set((mask or {}).get("exact") or [])      # matched as written: "Do", "Page" as names, not "do", "page"
    rx = re.compile(r"(?<![\w@.-])(?:%s)(?![\w@-]|\.\w)" % "|".join(("(?-i:%s)" if p[0] in exact else "%s") % re.escape(p[0])
                                                              for p in pairs), re.I) if pairs else None
    fwd = dict((p[0].lower(), p[1]) for p in pairs)
    rev = dict((p[1], p[0]) for p in pairs)
    words = set(w for p in pairs for w in _norm(p[0]).split()) - set(w for x in exact for w in _norm(x).split())
    _MASK_CACHE.insert(0, (pairs, rx, fwd, rev, words))
    del _MASK_CACHE[4:]
    return rx, fwd, rev, words


def apply_mask(text, mask):
    s = str(text or "")
    rx, fwd, _, _ = _mask_parts(mask)
    if rx is None:
        return s
    return rx.sub(lambda m: fwd.get(m.group(0).lower(), m.group(0)), s)


def unmask(text, mask):
    s = str(text or "")
    if not mask:
        return s
    rev = _mask_parts(mask)[2]
    return PLACEHOLDER.sub(lambda m: rev.get(m.group(0), m.group(0)), s)


def _unmask_deep(v, mask):
    if isinstance(v, str):
        return unmask(v, mask)
    if isinstance(v, list):
        return [_unmask_deep(x, mask) for x in v]
    if isinstance(v, dict):
        return dict((k, _unmask_deep(x, mask)) for k, x in v.items())
    return v


# ---- issues: which issue an incident is ---------------------------------------------------------------------

def desc_key(text, mask=None):
    """An incident's short description as a key for grouping: words only, without the masked values' words and anything
    with a digit (a host, a number) - "Account locked out - jsmith" and "- bdoe" are one key."""
    words = _mask_parts(mask)[3] if mask else set()
    return " ".join(w for w in _norm(text).split() if w not in words and not re.search(r"\d", w))[:160]


def category_issue(it):
    c, s = str(it.get("category") or "").strip(), str(it.get("subcategory") or "").strip()
    if c and s:
        return "%s / %s" % (c, s)
    return c or s or "(no category)"


def _window(r):
    w = (r or {}).get("window") or {}
    return _t(w.get("since")), _t(w.get("until")), _t(w.get("prev_since"))


def _in(dt, a, b):
    return dt is not None and a is not None and b is not None and a <= dt < b


def servicenow_insights_descriptions(r, mask=None, opts=None, state=None):
    """The incidents' descriptions ACT groups: one per key (the most frequent first, at most act_max_descriptions), with
    how many incidents have it and their most common category. -> [{id, key, text (masked), count, category}]. state
    (1.17.0): the keys it already places (the ACT cache) are not asked again."""
    o = opts or {}
    placed = (state or {}).get("classes") or {}
    since, until, prev = _window(r)
    agg = {}
    for it in (r or {}).get("items") or []:
        if it.get("table") != "incident" or not _in(_t(it.get("opened_at")), prev or since, until):
            continue
        k = desc_key(it.get("short_description"), mask)
        if not k:
            continue
        a = agg.get(k)
        if a is None:
            a = agg[k] = {"key": k, "first": it.get("short_description"), "count": 0, "cats": {}}
        a["count"] += 1
        cat = category_issue(it)
        a["cats"][cat] = a["cats"].get(cat, 0) + 1
    rows = sorted([a for a in agg.values() if a["key"] not in placed], key=lambda a: (-a["count"], a["key"]))[:int(_o(o, "act_max_descriptions", 2000))]
    out = []
    for i, a in enumerate(rows):
        out.append({"id": "D%d" % (i + 1), "key": a["key"], "text": apply_mask(a["first"], mask)[:200], "count": a["count"],
                    "category": sorted(a["cats"].items(), key=lambda kv: -kv[1])[0][0]})
    return out


def servicenow_insights_batches(rows, size=100):
    size = max(1, int(size or 100))
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def servicenow_insights_state(opts=None):
    """The answers gathered so far: {issues: [{name, about, kind, self_service}], classes: {key: issue}, fixes: {issue:
    {...}}, summaries: {scope: [lines]}, asked, answered, errors, model}."""
    return {"issues": [], "classes": {}, "fixes": {}, "summaries": {}, "asked": 0, "answered": 0, "errors": [], "model": "",
            "provider": str((opts or {}).get("provider") or "")}


def _parse_json(text):
    """ACT's answer -> its JSON object: the block between the markers, else a ```json block, else the outermost {...}.
    ansible-core 2.16 hands over an answer that is only JSON as a dict already."""
    if isinstance(text, dict):
        return text
    t = str(text or "")
    cands = []
    m = re.search(MARK_BEGIN + r"\s*(.*?)\s*" + MARK_END, t, re.S)
    if m:
        cands.append(m.group(1))
    cands += re.findall(r"```(?:json)?\s*(.*?)```", t, re.S)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    for c in cands:
        c = c.strip()
        for attempt in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                v = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(v, dict):
                return v
    return None


def _clean(s, limit=400):
    s = re.sub(r"\*\*|__|`+", "", str(s or ""))
    s = re.sub(r"^\s*(?:#+\s*|[-*•·]\s+|\d{1,2}[.)]\s+)", "", s)
    return " ".join(s.split()).strip()[:limit]


def _answer_note(raw, ran, reason):
    if not ran:
        return reason or "ACT did not run"
    return "" if _parse_json(raw) is not None else ("its answer has no readable JSON block" if str(raw or "").strip() else "it gave no answer")


def servicenow_insights_classify_evidence(batch, state, opts=None):
    """What ACT reads to group one batch: the issues so far, then each description with its count and category."""
    o = opts or {}
    lines = ["ISSUES SO FAR (%d of at most %s) - put a description in one of these when it fits:" % (
        len(state.get("issues") or []), _o(o, "act_max_issues", 40))]
    for x in state.get("issues") or []:
        lines.append("- %s | %s" % (x.get("name"), x.get("about") or ""))
    if not state.get("issues"):
        lines.append("(none yet)")
    lines += ["", "INCIDENT DESCRIPTIONS (id | incidents with it | ServiceNow category | short description):"]
    for d in batch:
        lines.append("%s | %d | %s | %s" % (d["id"], d["count"], d.get("category") or "-", d["text"]))
    return "\n".join(lines) + "\n"


def servicenow_insights_classify_task(opts=None):
    o = opts or {}
    return "\n".join([
        "Group IT service desk incidents into issues - the problem the user had - for a report to IT management. The evidence "
        "piped to you lists the issues defined so far and a batch of incident short descriptions (with how many incidents have "
        "each). You cannot run any commands in this task, and none are needed.",
        "",
        "For each description give the issue it belongs to. Use an issue from ISSUES SO FAR whenever it fits, with its name "
        "exactly as written there. Define a new issue only when none fits; at most %s issues in all, so prefer broader issues "
        "(\"Printer offline / print queue stuck\", not one per printer). An issue name is 3 to 8 words a manager understands, "
        "no ticket numbers, no host names, no people. A description too vague to place (\"help\", \"issue\") goes in "
        "\"Other / unclear\"." % _o(o, "act_max_issues", 40),
        "For each NEW issue also give: about (one short sentence), kind (\"request\" when it is really a request for something - "
        "an install, an account, access, hardware - rather than something broken; else \"incident\"), self_service (true when "
        "users could fix it themselves with a self-service tool or a short article, e.g. a password reset).",
        "Placeholders such as PERSON-3, HOST-12, IP-2 stand for masked values: keep them as they are, never guess them.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"new_issues": [{"name": "...", "about": "...", "kind": "incident", "self_service": false}], '
        '"items": [{"id": "D1", "issue": "..."}, {"id": "D2", "issue": "..."}]}',
        MARK_END,
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def servicenow_insights_classify_merge(state, raw, batch, opts=None, ran=True, reason="", model="", mask=None):
    """One batch's answer into the state: its new issues (up to act_max_issues; past that, the closest ... Other), each
    description's issue. A batch not answered leaves its descriptions to their category. An issue's name as the ACT
    cache keeps it (1.17.0, act_label_for_store: masked), so a run with the cache shows the same."""
    o = opts or {}
    st = json.loads(json.dumps(state or servicenow_insights_state()))
    st["asked"] = int(st.get("asked") or 0) + 1
    if model and not st.get("model"):
        st["model"] = model
    note = _answer_note(raw, ran, reason)
    if note:
        st["errors"].append("grouping, batch %s-%s: %s" % (batch[0]["id"] if batch else "?", batch[-1]["id"] if batch else "?", note))
        return st
    obj = _parse_json(raw) or {}
    st["answered"] = int(st.get("answered") or 0) + 1
    names = dict((_norm(x["name"]), x["name"]) for x in st["issues"])
    cap = int(_o(o, "act_max_issues", 40))
    for x in obj.get("new_issues") or obj.get("issues") or []:
        if not isinstance(x, dict):
            continue
        n = act_label_for_store(_clean(x.get("name"), 80), mask)
        if not n or _norm(n) in names or len(st["issues"]) >= cap:
            continue
        st["issues"].append({"name": n, "about": _clean(x.get("about"), 200),
                             "kind": "request" if str(x.get("kind") or "").strip().lower() == "request" else "incident",
                             "self_service": str(x.get("self_service")).strip().lower() in ("true", "1", "yes")})
        names[_norm(n)] = n
    by_id = dict((d["id"], d["key"]) for d in batch)
    for x in obj.get("items") or []:
        if not isinstance(x, dict):
            continue
        key = by_id.get(str(x.get("id") or "").strip())
        n = act_label_for_store(_clean(x.get("issue"), 80), mask)
        if not key or not n:
            continue
        hit = names.get(_norm(n))
        if hit is None:
            if len(st["issues"]) < cap:
                st["issues"].append({"name": n, "about": "", "kind": "incident", "self_service": False})
                names[_norm(n)] = hit = n
            else:
                hit = "Other / unclear"
        st["classes"][key] = hit
    return st


def _assign(r, state, mask, opts=None):
    """Every incident's issue: ACT's, by its description, else its category. -> {sys_id: issue}."""
    classes = (state or {}).get("classes") or {}
    out = {}
    for it in (r or {}).get("items") or []:
        if it.get("table") != "incident":
            continue
        n = classes.get(desc_key(it.get("short_description"), mask)) if classes else None
        out[it["sys_id"]] = n or category_issue(it)
    return out


# ---- the ACT cache (1.17.0): each description's issue kept in the report database, asked again only when new --------

def act_key_hash(key):
    """A description's key (desc_key) as the database holds it: its SHA-256, never the words."""
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()


def act_label_for_store(name, mask=None):
    """The one text the report database holds (1.17.0): ACT's short name for an issue - masked as the page masks (the
    people, hosts and addresses this run knows), then what the name itself holds (an email or IP address, a phone number, a
    host name), the placeholders as their kind ([person], [host] ...), at most 80 characters. Every stored label goes
    through here."""
    t = PLACEHOLDER.sub(lambda m: "[%s]" % m.group(1).lower(), apply_mask(name, mask))
    found = {}
    _scan(t, found, KEEP_WORDS)
    for real in sorted(found, key=len, reverse=True):
        t = t.replace(real, "[%s]" % found[real].lower())
    return " ".join(t.split())[:80]


def servicenow_insights_act_cache_key(opts=None, provider="", model=""):
    """What the cache is good for: the provider, the model and the grouping prompt (its hash) - another of any of them,
    and every description is asked again."""
    task = servicenow_insights_classify_task(opts)
    return "%s|%s|%s" % (str(provider or "").strip(), str(model or "").strip(), hashlib.sha256(task.encode("utf-8")).hexdigest()[:12])


def servicenow_insights_act_cached(state, r, mask, cached, opts=None):
    """The state with what the ACT cache holds (site_insights_db act_load: {issues: [{no, label, kind, self_service}],
    classes: {key hash: no}}): its issues first ("about" not kept), and each of this run's description keys it
    places. -> state with cache_nos {issue name: no} and cached (keys placed from the cache)."""
    st = json.loads(json.dumps(state or servicenow_insights_state()))
    c = cached if isinstance(cached, dict) else {}
    by_no = {}
    for x in c.get("issues") or []:
        n = _clean(x.get("label"), 80)
        if n and _norm(n) not in set(_norm(i["name"]) for i in st["issues"]):
            st["issues"].append({"name": n, "about": "", "kind": "request" if x.get("kind") == "request" else "incident",
                                 "self_service": bool(x.get("self_service"))})
        if n:
            by_no[int(x.get("no") or 0)] = n
    st["cache_nos"] = dict((v, k) for k, v in by_no.items())
    classes, hit = c.get("classes") or {}, 0
    if classes:
        since, until, prev = _window(r)
        for it in (r or {}).get("items") or []:
            if it.get("table") != "incident" or not _in(_t(it.get("opened_at")), prev or since, until):
                continue
            k = desc_key(it.get("short_description"), mask)
            if k and k not in st["classes"]:
                no = classes.get(act_key_hash(k))
                if no is not None and int(no) in by_no:
                    st["classes"][k] = by_no[int(no)]
                    hit += 1
    st["cached"] = hit
    return st


def servicenow_insights_act_cache_rows(state, mask=None):
    """What the ACT cache keeps after this run (site_insights_db act_save): {issues: [{no, label, kind, self_service}],
    classes: [[key hash, no]]} - an issue keeps its number; a new one takes the next. Never a description, a note or a
    fix: the keys as hashes, the issues by ACT's short name (act_label_for_store)."""
    st = state or {}
    nos = dict(st.get("cache_nos") or {})
    nxt = max(list(nos.values()) + [0]) + 1
    issues = []
    for x in st.get("issues") or []:
        if x["name"] not in nos:
            nos[x["name"]] = nxt
            nxt += 1
        issues.append({"no": nos[x["name"]], "label": act_label_for_store(x["name"], mask), "kind": x.get("kind") or "incident",
                       "self_service": bool(x.get("self_service"))})
    classes = [[act_key_hash(k), nos[n]] for k, n in sorted((st.get("classes") or {}).items()) if n in nos]
    return {"issues": issues, "classes": classes}


# ---- fixes: what each top issue's tickets say was done --------------------------------------------------------

def _issue_notes(r, issues_of, name, opts=None):
    """An issue's incidents done in the window or the one before: their close notes graded and counted (and their sys_ids,
    newest first, for the work notes)."""
    since, until, prev = _window(r)
    good, bare, total, ids = {}, 0, 0, []
    for it in sorted((r or {}).get("items") or [], key=lambda x: str(x.get("done_at") or ""), reverse=True):
        if it.get("table") != "incident" or issues_of.get(it["sys_id"]) != name:
            continue
        if not _in(_t(it.get("done_at")), prev or since, until):
            continue
        total += 1
        ids.append(it["sys_id"])
        g = grade(it.get("close_notes"), opts)
        if g == "bare":
            bare += 1
            continue
        k = _norm(it.get("close_notes"))[:300]
        e = good.setdefault(k, {"text": " ".join(str(it.get("close_notes") or "").split()), "count": 0, "grade": g})
        e["count"] += 1
    rows = sorted(good.values(), key=lambda e: (e["grade"] != "good", -e["count"]))
    return rows, bare, total, ids


def servicenow_insights_fix_batches(r, state, mask, opts=None):
    """The top issues ACT writes a fix for (act_fix_issues), act_fix_batch per question: [{items: [{id}], issues: [{id, name,
    count, notes: [[count, text (masked)]], bare, total, close_n (the close notes given), ids (its incidents done, newest
    first, at most 200 - for the work notes), work_notes: [], work_tickets: 0}]}]."""
    o = opts or {}
    since, until, _ = _window(r)
    issues_of = _assign(r, state, mask, o)
    counts = {}
    for it in (r or {}).get("items") or []:
        if it.get("table") == "incident" and _in(_t(it.get("opened_at")), since, until):
            n = issues_of.get(it["sys_id"])
            counts[n] = counts.get(n, 0) + 1
    top = [n for n, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if n != "Other / unclear"][:int(_o(o, "act_fix_issues", 15))]
    rows = []
    for i, n in enumerate(top):
        notes, bare, total, ids = _issue_notes(r, issues_of, n, o)
        given = notes[:int(_o(o, "act_notes", 40))]
        rows.append({"id": "I%d" % (i + 1), "name": n, "count": counts[n], "bare": bare, "total": total,
                     "notes": [[e["count"], apply_mask(e["text"], mask)[:400]] for e in given], "close_n": sum(e["count"] for e in given),
                     "ids": ids[:200], "work_notes": [], "work_tickets": 0})
    size = max(1, int(_o(o, "act_fix_batch", 3)))
    return [{"items": [{"id": x["id"]} for x in rows[j:j + size]], "issues": rows[j:j + size]} for j in range(0, len(rows), size)]


def servicenow_insights_fix_ids(batches):
    """The incidents whose work notes are read (0.96.0): every top issue's, once."""
    out, seen = [], set()
    for b in batches or []:
        for x in b.get("issues") or []:
            for k in x.get("ids") or []:
                if k not in seen:
                    seen.add(k)
                    out.append(k)
    return out


def servicenow_insights_fix_work_notes(batches, work_notes, mask=None, opts=None):
    """The batches with each issue's work notes (0.96.0): the newest act_work_notes_max of its incidents' (one of each
    text), masked, each cut to act_work_note_chars; work_tickets = how many incidents they come from. work_notes: the
    module's answer ({work_notes: {sys_id: [[when, text]]}})."""
    o = opts or {}
    wn = (work_notes or {}).get("work_notes", work_notes) if isinstance(work_notes, dict) else {}
    most, chars = int(_o(o, "act_work_notes_max", 20)), int(_o(o, "act_work_note_chars", 1500))
    out = json.loads(json.dumps(batches or []))
    for b in out:
        for x in b.get("issues") or []:
            got = []
            for k in x.get("ids") or []:
                for n in (wn or {}).get(k) or []:
                    if isinstance(n, (list, tuple)) and len(n) > 1 and str(n[1]).strip():
                        got.append((str(n[0]), k, " ".join(str(n[1]).split())))
            got.sort(key=lambda g: g[0], reverse=True)
            seen, keep, tickets = set(), [], set()
            for _, k, text in got:
                key = _norm(text)[:200]
                if not key or key in seen:
                    continue
                seen.add(key)
                keep.append(apply_mask(text, mask)[:chars])
                tickets.add(k)
                if len(keep) >= most:
                    break
            x["work_notes"], x["work_tickets"] = keep, len(tickets)
    return out


def servicenow_insights_fix_evidence(batch, mask=None, opts=None):
    lines = []
    for x in batch.get("issues") or []:
        lines += ["ISSUE %s: %s - %d incidents; %d resolved in the period, %d of them with a bare close note (\"fixed\", \"done\" ...)." % (
            x["id"], apply_mask(x["name"], mask), x["count"], x["total"], x["bare"]),
            "CLOSE NOTES THAT SAY SOMETHING (times used | note):"]
        lines += ["%d | %s" % (c, t) for c, t in x["notes"]] or ["(none)"]
        if x.get("work_notes"):
            lines += ["WORK NOTES - what was tried while the tickets were open, newest first (%d notes from %d tickets):" % (
                len(x["work_notes"]), x.get("work_tickets") or 0)]
            lines += ["- %s" % t for t in x["work_notes"]]
        lines.append("")
    return "\n".join(lines)


def servicenow_insights_fix_task(opts=None):
    o = opts or {}
    return "\n".join([
        "For each IT incident issue in the evidence piped to you, write the fix action a technician should follow, from the "
        "close notes of its tickets. You cannot run any commands in this task, and none are needed.",
        "",
        "Some issues also carry WORK NOTES: what technicians tried while the tickets were open. Read them for what was actually "
        "tried and what finally worked: prefer the steps that resolved tickets (the last thing tried before the ticket was "
        "resolved, or what its close note confirms), and leave out what was tried and did not help. Count a step's support "
        "from the close notes and the work notes together.",
        "",
        "For each issue give:",
        "- steps: 2 to 6 short imperative steps, in order (\"Restart the Print Spooler on the print server.\"). Take them from the "
        "close notes whenever they show what was done; generic, never a ticket number, a person or one machine's name;",
        "- support: for each step, how many of the notes show it (0 when you added it);",
        "- source: \"tickets\" when the notes show every step, \"mixed\" when you had to fill gaps, \"suggested\" when the notes say "
        "too little and the steps are your likely fix (they will be marked: not verified);",
        "- about: one short sentence on what the issue is, as a manager reads it (no ticket numbers, hosts or people);",
        "- why: one sentence on where the steps come from and how much the notes agree;",
        "- disagree: true when the notes show clearly different fixes for the same issue (the fix is not known);",
        "- tickets: how many tickets the steps worked on, as the close notes and work notes show (0 when they do not say);",
        "- workaround: when the notes show the same workaround applied again and again while the cause stays (a restart, a "
        "re-image, a manual reset), one sentence naming it and how many tickets used it; else null. A repeated workaround makes "
        "the issue a knowledge article candidate;",
        "- kb: a knowledge article draft {title, symptoms, cause, steps} - ONLY when the issue has at least %s incidents and the "
        "notes agree on the fix (source tickets or mixed), or the notes show a repeated workaround on 5 or more tickets; else "
        "null. Symptoms: what the user sees; cause: the usual cause; steps as above; tickets: how many tickets these steps worked "
        "on (the draft says it)." % _o(o, "kb_min", 50),
        "Placeholders such as PERSON-3, HOST-12 stand for masked values: keep them out of the steps. Never suggest deleting data, "
        "reimaging or anything destructive without saying so in the step. No markdown.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"items": [{"id": "I1", "steps": ["...", "..."], "support": [12, 3], "source": "tickets", "about": "...", "why": "...", "disagree": false, '
        '"tickets": 14, "workaround": null, "kb": {"title": "...", "symptoms": "...", "cause": "...", "steps": ["..."], "tickets": 14}}]}',
        MARK_END,
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def _int(v):
    s_ = str(v if v is not None else "").strip()
    return int(s_) if s_.isdigit() else 0


def servicenow_insights_work_notes_state(state, answer, wanted=0, on=True):
    """What was read of the work notes (0.96.0), kept in the state for the page: {on, asked (incidents), read (incidents
    with notes), error}."""
    st = json.loads(json.dumps(state or servicenow_insights_state()))
    a = answer if isinstance(answer, dict) else {}
    st["work_notes"] = {"on": bool(on), "asked": int(wanted or 0), "read": len(a.get("work_notes") or {}),
                        "error": str(a.get("work_notes_error") or a.get("msg") or "") if (a.get("failed") or a.get("work_notes_error")) else ""}
    return st


def servicenow_insights_fix_merge(state, raw, batch, opts=None, ran=True, reason="", model=""):
    st = json.loads(json.dumps(state or servicenow_insights_state()))
    st["asked"] = int(st.get("asked") or 0) + 1
    if model and not st.get("model"):
        st["model"] = model
    note = _answer_note(raw, ran, reason)
    names = dict((x["id"], x["name"]) for x in batch.get("issues") or [])
    read = dict((x["id"], {"close": int(x.get("close_n") or 0), "work": len(x.get("work_notes") or []),
                           "work_tickets": int(x.get("work_tickets") or 0)}) for x in batch.get("issues") or [])
    if note:
        st["errors"].append("fixes for %s: %s" % (", ".join(names.values()), note))
        return st
    st["answered"] = int(st.get("answered") or 0) + 1
    obj = _parse_json(raw) or {}
    for x in obj.get("items") or []:
        if not isinstance(x, dict) or str(x.get("id") or "") not in names:
            continue
        steps = [_clean(s, 300) for s in (x.get("steps") or []) if _clean(s, 300)][:8]
        if not steps:
            continue
        src = str(x.get("source") or "").strip().lower()
        src = {"tickets": "tix", "mixed": "mix", "suggested": "ai"}.get(src, "mix")
        kb = x.get("kb") if isinstance(x.get("kb"), dict) else None
        if kb:
            kb = {"title": _clean(kb.get("title"), 120), "symptoms": _clean(kb.get("symptoms"), 600), "cause": _clean(kb.get("cause"), 600),
                  "steps": [_clean(s, 300) for s in (kb.get("steps") or []) if _clean(s, 300)][:10], "tickets": _int(kb.get("tickets"))}
            if not kb["title"] or not kb["steps"]:
                kb = None
        support = [int(s) if str(s).strip().lstrip("-").isdigit() else 0 for s in (x.get("support") or [])]
        wa = x.get("workaround")
        wa = _clean(wa, 300) if wa and str(wa).strip().lower() not in ("null", "none", "false") else ""
        st["fixes"][names[str(x["id"])]] = {"steps": steps, "support": support, "source": src, "why": _clean(x.get("why"), 400),
                                            "about": _clean(x.get("about"), 200),
                                            "disagree": str(x.get("disagree")).strip().lower() in ("true", "1", "yes"), "kb": kb,
                                            "tickets": _int(x.get("tickets")), "workaround": wa, "read": read[str(x["id"])]}
    return st


# ---- the model: every number of the report, for the site and for each team -------------------------------------

def servicenow_insights_teams(r, opts=None):
    """The teams with their own page: the groups named, else the groups with team_min tickets in the window (the busiest
    first), at most teams_max."""
    o = opts or {}
    named = name_list(o.get("groups"))
    since, until, _ = _window(r)
    vol = {}
    for it in (r or {}).get("items") or []:
        g = it.get("group") or ""
        if g and (_in(_t(it.get("opened_at")), since, until) or _in(_t(it.get("done_at")), since, until)):
            vol[g] = vol.get(g, 0) + 1
    if named:
        found = dict((g.lower(), g) for g in vol)
        return [found.get(n.lower(), n) for n in named]
    rows = [g for g, n in sorted(vol.items(), key=lambda kv: (-kv[1], kv[0])) if n >= int(_o(o, "team_min", 25))]
    return rows[:int(_o(o, "teams_max", 20))]


# ---- sites, directorates and teams (0.97.0): every assignment group's place, from its name --------------------------

STAFF_CODE = re.compile(r"^[A-Za-z]{1,2}\d{1,4}$")             # J6, J63, J633, A6, N6, G6 (not IPv6, Win10)
OTHER, UNKNOWN = "Other", "Unknown"
PLACED_BY = {"map": "map", "suffix": "name rule", "tag": "name rule", "code": "name rule", "named": "name rule",
             "parent": "ServiceNow parent", "parent-suffix": "ServiceNow parent", "parent-code": "ServiceNow parent",
             "other": "Other", "unknown": "Unknown", "names-fallback": "name (the tree not read)"}


def _ci_get(d, key):
    """A dict's value by its key in any case and spacing (the user's map, aliases, tags)."""
    k = " ".join(str(key or "").split()).lower()
    for dk, v in (d or {}).items():
        if " ".join(str(dk).split()).lower() == k:
            return v
    return None


def _parts(name):
    """A name split at " - " (a space each side: SITE-C is one part)."""
    full = " ".join(str(name or "").split())
    return [p.strip() for p in re.split(r"\s+-\s+", full) if p.strip()] or [full]


def _code_in(text):
    """The first staff code among a text's words (J633 in "J633 Network Ops")."""
    for w in re.findall(r"[A-Za-z0-9]+", str(text or "")):
        if STAFF_CODE.match(w):
            return w.upper()
    return ""


def hier_place(name, opts=None, chain=None, roots=None, tree_ok=True):
    """One assignment group's place -> {site, dir, team, rule, under}, by servicenow_insights_hierarchy_source: parents
    (ServiceNow's group tree, the default) or names (the rules); the map, known_groups_only after either."""
    o = opts or {}
    if str(o.get("hierarchy_source") or "parents").strip().lower() == "names":
        h = hier_place_names(name, o, chain)
    else:
        h = hier_place_parents(name, o, chain, roots, tree_ok)
    full = " ".join(str(name or "").split())
    m = _ci_get(o.get("hierarchy_map"), full)
    if isinstance(m, dict):
        h.update({"site": " ".join(str(m.get("site") or h["site"]).split()), "dir": " ".join(str(m.get("directorate") or m.get("dir") or h["dir"]).split()),
                  "team": " ".join(str(m.get("team") or h["team"]).split()), "rule": "map"})
    elif o.get("known_groups_only"):
        known = set(" ".join(str(v.get("site") or "").split()).lower() for v in (o.get("hierarchy_map") or {}).values() if isinstance(v, dict))
        h.update({"site": h["site"] if h["site"].lower() in known else UNKNOWN, "dir": UNKNOWN, "rule": "unknown"})
    h.setdefault("under", "")
    return h


def hier_place_parents(name, opts=None, chain=None, roots=None, tree_ok=True):
    """By ServiceNow's group tree: the site is the root (the top parent; servicenow_insights_site_roots names them, else
    any parentless group with children), shown by its first " - " part; the directorate the parent one below the root -
    a group right under the root has the root as its directorate (its name after the site, or "(site direct)"); a team
    under a team is still a team of that directorate ("under" its parent team). No way up to a root: Unknown (its site by
    its name, when that is a root's). sys_user_group not read: the site by the name, the directorate "(unknown)"."""
    o = opts or {}
    full = " ".join(str(name or "").split())
    parts = _parts(full)
    rest = " - ".join(parts[1:]) if len(parts) >= 2 else full
    if not tree_ok:
        return {"site": parts[0] if len(parts) >= 2 else UNKNOWN, "dir": "(unknown)", "team": rest, "rule": "names-fallback", "under": ""}
    wanted = set(" ".join(str(x).split()).lower() for x in (o.get("site_roots") or []) if str(x).strip())
    roots = set(roots or [])
    anc = [" ".join(str(a).split()) for a in (chain or []) if str(a or "").strip()]
    if wanted:
        cut = next((i for i, a in enumerate(anc) if a.lower() in wanted), None)
        anc = anc[:cut + 1] if cut is not None else []
        is_root = full.lower() in wanted
    else:
        is_root = full in roots
        if anc and anc[-1] not in roots:              # the chain stopped short of a root (a loop, too deep, not read)
            anc = []
    root_sites = set(_parts(r)[0].lower() for r in (wanted or roots))
    if is_root and not anc:
        rp = _parts(full)
        return {"site": rp[0], "dir": " - ".join(rp[1:]) or "(site direct)", "team": full, "rule": "parent", "under": ""}
    if not anc:
        return {"site": parts[0] if len(parts) >= 2 and parts[0].lower() in root_sites else UNKNOWN, "dir": UNKNOWN, "team": full,
                "rule": "unknown", "under": ""}
    root = anc[-1]
    rp = _parts(root)
    site = rp[0]
    below = anc[:-1]

    def short(g):
        gp = _parts(g)
        return " - ".join(gp[1:]) if len(gp) >= 2 and gp[0].lower() == site.lower() else g
    d = short(below[-1]) if below else (" - ".join(rp[1:]) or "(site direct)")
    team = short(full)
    return {"site": site, "dir": d, "team": team, "rule": "parent", "under": short(below[0]) if len(below) >= 2 else ""}


def hier_place_names(name, opts=None, chain=None):
    """By the names (servicenow_insights_hierarchy_source: names). chain: its parents in ServiceNow, nearest first, the
    site's root last (sys_user_group). In order: (1) servicenow_insights_hierarchy_map {group: {site, directorate,
    team}}; (2) a word of servicenow_insights_suffix_directorates - the name, or a parent below the root, ends in
    "- WORD" (any case): that directorate; (3)
    servicenow_insights_directorate_tags {word: directorate} - a whole word of the name; (4) a staff code (letters then
    digits: J633, A6) in the name after the site, then a second part named in servicenow_insights_directorates, then
    the nearest parent's staff code (the root is never a directorate); (5) the first parent below the root; (6) Other.
    The site is the root's first " - " part, else the name's. servicenow_insights_directorate_alias {from: to} renames
    or merges last. With servicenow_insights_known_groups_only, a group not in the map is Unknown (its site's when the
    name starts with a known site)."""
    o = opts or {}
    full = " ".join(str(name or "").split())
    parts = _parts(full)
    anc = [" ".join(str(a).split()) for a in (chain or []) if str(a or "").strip()]
    below = anc[:-1]                                             # the parents below the site's root
    site = _parts(anc[-1])[0] if anc else (parts[0] if len(parts) >= 2 else OTHER)
    rest = parts[1:] if len(parts) >= 2 else parts
    named = dict((" ".join(str(d).split()).lower(), " ".join(str(d).split())) for d in (o.get("directorates") or []) if str(d or "").strip())
    words = set(w.lower() for w in re.findall(r"[A-Za-z0-9]+", full))
    tag = next((v for k, v in sorted((o.get("directorate_tags") or {}).items(), key=lambda kv: str(kv[0]).lower())
                if str(k).strip() and all(w in words for w in re.findall(r"[A-Za-z0-9]+", str(k).lower()))), None)
    code = _code_in(" ".join(rest)) if len(parts) >= 2 else ""
    d, rule = "", ""
    sfx = [" ".join(str(w).split()) for w in (o.get("suffix_directorates") or []) if str(w or "").strip()]

    def ends(text):
        return next((w for w in sfx if re.search(r"\s-\s*%s\s*$" % re.escape(w), text, re.I)), None)
    suffix = ends(full)
    if suffix:
        d, rule = suffix, "suffix"
    elif any(ends(a) for a in below):
        d, rule = next(ends(a) for a in below if ends(a)), "parent-suffix"
    elif tag:
        d, rule = " ".join(str(tag).split()), "tag"
    elif code:
        d, rule = code, "code"
    elif len(parts) >= 3 and parts[1].lower() in named:
        d, rule = named[parts[1].lower()], "named"
    elif any(_code_in(" ".join(_parts(a)[1:])) for a in below):
        d, rule = next(_code_in(" ".join(_parts(a)[1:])) for a in below if _code_in(" ".join(_parts(a)[1:]))), "parent-code"
    elif below:
        top = below[-1]
        tp = _parts(top)
        d, rule = " - ".join(tp[1:]) if len(tp) > 1 and tp[0].lower() == site.lower() else top, "parent"
    else:
        d, rule = OTHER, "other"
    # the team: the name without its site, a directorate suffix and a second part that only says its directorate
    t = list(rest)
    if t and len(t) > 1 and t[-1].strip().lower() in [w.lower() for w in sfx]:
        t = t[:-1]
    if len(t) > 1 and (t[0].upper() == d.upper() or t[0].lower() in named):
        t = t[1:]
    team = " - ".join(t) or full
    alias = _ci_get(o.get("directorate_alias"), d)
    if alias and str(alias).strip():
        d = " ".join(str(alias).split())
    return {"site": site or OTHER, "dir": d or OTHER, "team": team or full, "rule": rule, "under": ""}


def group_roots(tree):
    """The roots of ServiceNow's group tree as read: parentless groups that are someone's parent."""
    t = tree or {}
    parents = set(v.get("parent") for v in t.values() if v.get("parent"))
    return set(v.get("name") for k, v in t.items() if not v.get("parent") and k in parents)


def group_chain(group, tree, depth=8):
    """A group's parents in ServiceNow, nearest first (the root last), from sys_user_group as the module read it
    ({sys_id: {name, parent}}); a cycle or a parent not read stops it."""
    by_name = dict((v.get("name"), k) for k, v in (tree or {}).items())
    k = by_name.get(group)
    out, seen = [], set([k])
    p = ((tree or {}).get(k) or {}).get("parent") if k else ""
    while p and p in (tree or {}) and p not in seen and len(out) < int(depth):
        seen.add(p)
        out.append(tree[p].get("name") or "")
        p = tree[p].get("parent")
    return [x for x in out if x]


# ---- the org map (1.8.0): servicenow_insights_hierarchy_source: map - management's own tree, any depth --------------------
# Every group is placed only by servicenow_insights_hierarchy_map, {"<group>": ["Site", "Level1", ...]} (a list: the names
# may hold "/" and "&") or "Site > Level1 > ..."; the group is the leaf under its levels. A group with tickets that is not in
# the map goes to servicenow_insights_unmapped_bucket (Unknown); tickets with no group to Unassigned. Both last.
ORG_SEP = "›"                  # between a node's levels in its key: never in a group's name the way "/" or ">" may be
UNASSIGNED = "Unassigned"
OUTCOMES = ["Fixed", "Workaround", "Duplicate", "Known Error", "No Resolution Provided"]


def org_levels(v):
    """One map value -> its levels: a list ["Site", "Level1", ...], a string "Site > Level1 > ...", or the older
    {site, directorate} form. Blank levels are left out."""
    if isinstance(v, (list, tuple)):
        lv = [" ".join(str(x).split()) for x in v]
    elif isinstance(v, dict):
        lv = [" ".join(str(v.get(k) or "").split()) for k in ("site", "directorate", "dir")]
    else:
        lv = [" ".join(x.split()) for x in str(v or "").split(">")]
    return [x for x in lv if x]


def org_map(m):
    """servicenow_insights_hierarchy_map -> ({group, lower case: (group, levels)}, [groups with no level])."""
    out, bad = {}, []
    for g, v in (m or {}).items():
        name = " ".join(str(g or "").split())
        if not name:
            continue
        lv = org_levels(v)
        if lv:
            out[name.lower()] = (name, lv)
        else:
            bad.append(name)
    return out, sorted(bad, key=_nat)


def org_is_map(o):
    return str((o or {}).get("hierarchy_source") or "parents").strip().lower() == "map"


def org_hierarchy(names, opts=None):
    """Every group's place by the map -> {group: {site, dir, team, path, rule, by, under}}: site the first level, dir the
    rest (" > "), path every level; not in the map: the unmapped bucket, rule unknown."""
    o = opts or {}
    m, _ = org_map(o.get("hierarchy_map"))
    bucket = " ".join(str(o.get("unmapped_bucket") or UNKNOWN).split()) or UNKNOWN
    out = {}
    for n in names or []:
        g = " ".join(str(n or "").split())
        if not g:
            continue
        hit = m.get(g.lower())
        path = hit[1] if hit else [bucket]
        out[str(n)] = {"site": path[0], "dir": " > ".join(path[1:]), "team": str(n), "path": list(path),
                       "rule": "map" if hit else "unknown", "by": "map" if hit else PLACED_BY["unknown"], "under": ""}
    return out


def org_teams(r, opts=None):
    """With the map, every group in it that has tickets has its own page (the map is the list of teams)."""
    m, _ = org_map((opts or {}).get("hierarchy_map"))
    seen = set(it.get("group") for it in (r or {}).get("items") or [] if it.get("group"))
    return sorted([g for g in seen if " ".join(str(g).split()).lower() in m], key=_nat)


def org_tree(hier, groups, unassigned=False, opts=None):
    """The tree of the groups' paths -> [node]: {key, label, path, depth, groups (directly under it), all (every group
    below it), children}. A to Z at every level, the unmapped bucket and Unassigned last; Unassigned (tickets with no
    group) holds the group ""."""
    o = opts or {}
    bucket = " ".join(str(o.get("unmapped_bucket") or UNKNOWN).split()) or UNKNOWN
    root = {"children": {}, "groups": []}
    for g in groups:
        node = root
        for lv in (hier.get(g) or {}).get("path") or [bucket]:
            node = node["children"].setdefault(lv, {"children": {}, "groups": []})
        node["groups"].append(g)
    if unassigned:
        root["children"].setdefault(UNASSIGNED, {"children": {}, "groups": []})["groups"].append("")

    def last(name, depth):
        return (depth == 1 and name in (bucket, UNASSIGNED), name == UNASSIGNED) + tuple(_nat(name))

    def build(n, path):
        out = []
        for name in sorted(n["children"], key=lambda x: last(x, len(path) + 1)):
            c = n["children"][name]
            p = path + [name]
            kids = build(c, p)
            mine = sorted(c["groups"], key=_nat)
            out.append({"key": "org:" + ORG_SEP.join(p), "label": name, "path": p, "depth": len(p), "groups": mine,
                        "all": sorted(set(mine) | set(g for k in kids for g in k["all"]), key=_nat), "children": kids})
        return out
    return build(root, [])


def org_walk(nodes):
    for n in nodes or []:
        yield n
        for x in org_walk(n["children"]):
            yield x


def custom_rows(items, since, until, groups, opts=None, base=None, tz=None, spec=None):
    """The Custom view's data (1.8.0): one compact row per incident and requested item open at the window's start or after
    - columns, so 20,000 tickets stay small - from which the page works out the figures for any choice of groups: g the
    group's index in groups (-1: none), t 0 incident / 1 requested item, p the priority, o / d opened / done in seconds from
    the window's start (d null: open), s made_sla (the resolution SLA) 1 / 0 / -1; and the 1.7.0 fields where the tickets
    have them: resp (the response SLA) and noslo 1 / 0 / -1, hops (reassignment_count; incidents), hold (on hold now), r7
    (reopened within 7 days), out (the outcome's index in outcomes; -1 not resolved), risk (1 at risk, 2 breached).
    1.43.0: vip, where any ticket has a VIP tier - its tier's number (1 the first tier listed; 0 not VIP).
    1.10.0: from base on (the window before's start, for the periods' compare), and 1.9.0's where the tickets have them:
    tis (the time in each state, tenths of hours: new, progress, hold, resolved; null not measured), ob (the opened day's
    business-day count on the calendar: the backlog's age at any period's end); the day calendar and the periods.
    -> {groups, n, until, cols: {name: [values]}, hop_limit, outcomes, unable (their indices), calendar, periods}"""
    base = base or since
    tz = tz or UTC
    o = opts or {}
    gi = dict((g, i) for i, g in enumerate(groups))
    flow = []
    for x in items or []:
        if x.get("table") not in ("incident", "sc_req_item"):
            continue
        op, dn = _t(x.get("opened_at")), _t(x.get("done_at"))
        if op and op < until and not (dn and dn < base):
            flow.append((x, op, dn))
    has = lambda k: any(k in x for x, _, _ in flow)                               # noqa: E731
    cols = dict((k, []) for k in ("g", "t", "p", "o", "d", "s"))
    extra = [(k, f) for k, f in (("resp", "_resp"), ("noslo", "_no_slo"), ("hops", "_hops"), ("hold", "_hold_now"), ("r7", "_reopen7"),
                                 ("out", "_outcome"), ("risk", "_risk")) if has(f)]
    for k, _ in extra:
        cols[k] = []
    if has("_tis"):
        cols["tis"] = []
    vip_on = any(x.get("_vip") is not None for x, _, _ in flow)
    if vip_on:
        cols["vip"] = []
    cal = day_calendar(base, until, since, spec, tz)
    cols["ob"] = []
    names = list(OUTCOMES)
    bdo = {}

    def tri(v):
        return -1 if v is None else (1 if v else 0)
    for x, op, dn in flow:
        inc = x["table"] == "incident"
        cols["g"].append(gi.get(x.get("group") or "", -1))
        cols["t"].append(0 if inc else 1)
        try:
            cols["p"].append(int(x.get("priority_value") or 0))
        except (TypeError, ValueError):
            cols["p"].append(0)
        cols["o"].append(int((op - since).total_seconds()))
        cols["d"].append(int((dn - since).total_seconds()) if dn else None)
        cols["s"].append(tri(x.get("made_sla")))
        for k, f in extra:
            v = x.get(f)
            if k == "hops":
                try:
                    cols[k].append(int(v or 0) if inc else 0)
                except (TypeError, ValueError):
                    cols[k].append(0)
            elif k == "out":
                if inc and v:
                    if v not in names:
                        names.append(v)
                    cols[k].append(names.index(v))
                else:
                    cols[k].append(-1)
            elif k == "risk":
                cols[k].append((2 if v.get("breached") else 1) if isinstance(v, dict) else 0)
            elif k == "hold":
                cols[k].append(1 if inc and v else 0)
            else:
                cols[k].append(tri(v))
        if vip_on:
            cols["vip"].append(0 if x.get("_vip") is None else x["_vip"] + 1)
        if "tis" in cols:
            ts = x.get("_tis") if inc else None
            cols["tis"].append([int(round(10 * float(ts.get(k_) or 0))) for k_ in STATE_KINDS] if isinstance(ts, dict) else None)
        od = op.astimezone(tz).date()
        if od not in bdo:
            bdo[od] = _bd_ord(od, set(cal["days"]), cal["hol"]) - cal["base"]
        cols["ob"].append(bdo[od])
    unable = set(str(u).lower() for u in name_list(o.get("unable_outcomes") or ["Known Error", "No Resolution Provided"]))
    plan = period_plan(since, until, base, tz, o)
    return {"groups": list(groups), "n": len(flow), "until": int((until - since).total_seconds()), "cols": cols,
            "hop_limit": int(_o(o, "hop_limit", 3)), "outcomes": names, "unable": [i for i, n_ in enumerate(names) if n_.lower() in unable],
            "calendar": {"d0": cal["d0"], "mid": cal["mid"], "bo": cal["bo"],
                         "first": since.astimezone(tz).date().isoformat(), "last": (until - datetime.timedelta(seconds=1)).astimezone(tz).date().isoformat()},
            "periods": [dict(p, a=int((p["a"] - since).total_seconds()), b=int((p["b"] - since).total_seconds()),
                             ca=int((p["ca"] - since).total_seconds()) if p["ca"] else None,
                             cb=int((p["cb"] - since).total_seconds()) if p["cb"] else None)
                        for p in plan[0]], "left": plan[1]}


def custom_presets(hier, groups, split=None, opts=None):
    """The Custom view's one-click choices (1.8.0): the split's "<label>" and "non-<label>", then
    servicenow_insights_custom_view_presets {name: {levels: [...]}} (the groups whose map path has every level, in any
    position) or {name: {pattern: "..."}} (by name, * and ?, any case). -> [{name, idx}] (idx: indices into groups)."""
    gi = dict((g, i) for i, g in enumerate(groups))
    out = []
    if split and split.get("label"):
        inn = set(split.get("groups") or [])
        out.append({"name": split["label"], "idx": [gi[g] for g in groups if g in inn]})
        out.append({"name": "non-" + split["label"], "idx": [gi[g] for g in groups if g not in inn]})
    for name, spec in sorted(((opts or {}).get("custom_view_presets") or {}).items(), key=lambda kv: _nat(kv[0])):
        spec = spec if isinstance(spec, dict) else {}
        lv = [x.lower() for x in org_levels(spec.get("levels") or [])]
        pat = str(spec.get("pattern") or "").strip().lower()
        idx = [gi[g] for g in groups if (lv and all(x in [y.lower() for y in (hier.get(g) or {}).get("path") or []] for x in lv))
               or (pat and g and fnmatch.fnmatchcase(g.lower(), pat))]
        out.append({"name": " ".join(str(name).split()), "idx": idx})
    return [x for x in out if x["name"]]


def custom_figures(data, sel, period=None, vip=None):
    """The Custom view's figures for the groups whose indices are in sel - what the page's script works out, the same
    way as scope_model (the tests compare them): incidents and requests opened, incidents resolved, the backlog at the
    period's end and start, the median hours to resolve, SLA met %. period (1.10.0): (a, b) in seconds from the window's
    start, the window itself when None. What is known only now (on hold, open over the hop limit) only for a period that
    ends at the report's end; None otherwise. vip (1.43.0, the VIP only choice): None every ticket, 0 any VIP tier, k the
    tier k (1 the first listed)."""
    c, w = data["cols"], data["until"]
    a, b = period if period else (0, w)
    now = b == w
    sel = set(sel)
    if vip is not None:            # the tickets of other tiers as if of no group chosen (-2: never an index)
        vc = c.get("vip") or [0] * len(c["g"])
        c = dict(c, g=[g if (vc[i] == vip if vip else vc[i] > 0) else -2 for i, g in enumerate(c["g"])])
    inc = req = res = bl = bl0 = sla_n = sla_ok = 0
    hrs = []
    for i in range(len(c["g"])):
        if c["g"][i] not in sel:
            continue
        o, d, t = c["o"][i], c["d"][i], c["t"][i]
        if a <= o < b:
            if t == 0:
                inc += 1
            else:
                req += 1
        done_in = d is not None and a <= d < b
        if t == 0 and done_in:
            res += 1
            if d >= o:
                hrs.append((d - o) / 3600.0)
        if o <= b and (d is None or d > b):
            bl += 1
        if o <= a and (d is None or d > a):
            bl0 += 1
        if done_in and c["s"][i] in (0, 1):
            sla_n += 1
            sla_ok += c["s"][i]
    out = {"incidents": inc, "requests": req, "resolved": res, "backlog": [bl, bl0], "median_h": _r1(_median(hrs)),
           "sla": _r1(_pct(sla_ok, sla_n))}
    # the 1.7.0 figures, where the rows carry them - as scope_model: the response SLA met of what was done in the period
    # (no SLA left out), done with no SLA, incidents open now over the hop limit (and the share of those resolved), on hold
    # now, unable to resolve and first-contact resolution of the incidents resolved
    rs, ns, hop, hres, hold, una, fcr = [0, 0], 0, 0, 0, 0, 0, 0
    lim, unable = int(data.get("hop_limit", 3)), set(data.get("unable") or [])
    # 1.9.0's, where the rows carry them: the time in each state of the incidents resolved in the period (tenths of
    # hours), the backlog's age in business days at the period's end (the day calendar)
    tis_n, tis = 0, [0, 0, 0, 0]
    cal = data.get("calendar") or {}
    mid, bo = cal.get("mid") or [], cal.get("bo") or []
    age = [0, 0, 0, 0]
    end_bo = None
    if mid and "ob" in c:
        k = bisect.bisect_right(mid, b) - 1
        end_bo = bo[max(k, 0)]
    for i in range(len(c["g"])):
        if c["g"][i] not in sel:
            continue
        o, d, t = c["o"][i], c["d"][i], c["t"][i]
        done_in = d is not None and a <= d < b
        open_end = o <= b and (d is None or d > b)
        if done_in and "resp" in c and c["resp"][i] in (0, 1):
            rs[1] += 1
            rs[0] += c["resp"][i]
        if done_in and "noslo" in c and c["noslo"][i] == 1:
            ns += 1
        if t == 0 and "hops" in c:
            over = c["hops"][i] > lim + 1
            if open_end and over:
                hop += 1
            if done_in:
                hres += 1 if over else 0
                fcr += 1 if c["hops"][i] == 0 else 0
        if t == 0 and open_end and "hold" in c and c["hold"][i] == 1:
            hold += 1
        if t == 0 and done_in and "out" in c and c["out"][i] in unable:
            una += 1
        if t == 0 and done_in and "tis" in c and c["tis"][i] is not None:
            tis_n += 1
            for j in range(4):
                tis[j] += c["tis"][i][j]
        if end_bo is not None and open_end:
            bd = max(end_bo - c["ob"][i], 0)
            age[0 if bd <= 1 else (1 if bd <= 5 else (2 if bd <= 10 else 3))] += 1
    if "resp" in c:
        out["resp"] = _r1(_pct(rs[0], rs[1]))
    if "noslo" in c:
        out["no_slo"] = ns
    if "hops" in c:
        out.update(hops_open=hop if now else None, hops_done_pct=_r1(_pct(hres, res)), fcr=_r1(_pct(fcr, res)))
    if "hold" in c:
        out["hold"] = hold if now else None
    if "out" in c:
        out["unable"] = _r1(_pct(una, res))
    if "tis" in c:
        out["tis"] = dict((k_, _r1(tis[j] / 10.0 / tis_n) if tis_n else None) for j, k_ in enumerate(STATE_KINDS))
        out["tis_n"] = tis_n
    if end_bo is not None:
        out["aging"] = age
    return out


PERIODS = ("all", "this_month", "1m", "2m", "3m", "6m", "9m", "12m", "1w", "2w", "4w")     # the default (1.16.0)
PERIOD_LABELS = {"all": "The window", "this_month": "This month"}
PERIOD_MAX = {"m": 12, "w": 8}


def period_name(nm):
    """'all', 'this_month', Nm (1 to 12 months) or Nw (1 to 8 weeks) -> (kind '' / 'm' / 'w', N, label) - else ValueError."""
    if nm in PERIOD_LABELS:
        return "", 0, PERIOD_LABELS[nm]
    m = re.match(r"^(\d{1,2})([mw])$", nm)
    if not m or not 1 <= int(m.group(1)) <= PERIOD_MAX[m.group(2)]:
        raise ValueError("servicenow_insights_periods: %s is not a period - all, this_month, 1m to 12m (calendar months) or 1w to 8w "
                         "(weeks)" % nm)
    k, unit = int(m.group(1)), "month" if m.group(2) == "m" else "week"
    return m.group(2), k, ("Last %s" % unit) if k == 1 else ("Last %d %ss" % (k, unit))
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _midnight(d, tz):
    return datetime.datetime(d.year, d.month, d.day, tzinfo=tz)


def _month(d, k=0):
    """The first day of d's month, k months later (earlier when negative)."""
    m = d.year * 12 + d.month - 1 + k
    return datetime.date(m // 12, m % 12 + 1, 1)


def period_title(a, b, tz):
    """[a, b) in words: whole calendar months by name ("October 2026", "September to October 2026"), else the dates."""
    la, lb = a.astimezone(tz), b.astimezone(tz)
    last = (b - datetime.timedelta(seconds=1)).astimezone(tz).date()
    whole = la.time() == datetime.time(0) and la.day == 1 and lb.time() == datetime.time(0) and lb.day == 1
    if whole:
        first = la.date()
        if (first.year, first.month) == (last.year, last.month):
            return "%s %d" % (MONTHS[first.month - 1], first.year)
        if first.year == last.year:
            return "%s to %s %d" % (MONTHS[first.month - 1], MONTHS[last.month - 1], last.year)
        return "%s %d to %s %d" % (MONTHS[first.month - 1], first.year, MONTHS[last.month - 1], last.year)
    return "%s to %s" % (la.date().isoformat(), last.isoformat())


def period_bounds(since, until, prev, tz, opts=None):
    """The Custom view's periods - the periods alone (period_plan)."""
    return period_plan(since, until, prev, tz, opts)[0]


def period_plan(since, until, prev, tz, opts=None):
    """The Custom view's periods (1.10.0; any Nm and Nw since 1.16.0): calendar months and Mon-Sun weeks, not rolling
    days. -> ([{name, label, kind, n, title, a, b, ca, cb, ctitle}] as datetimes, [{name, why}] left out). prev: where the
    rows start. all and this_month: ca / cb None when the period before is not in the rows.
      all         the window, against the window before
      this_month  the report end's month so far, against the same days of the month before
      Nm          the last N whole calendar months (1 to 12) up to the report end, against the N before
      Nw          the last N whole weeks (1 to 8; servicenow_insights_week_start: mon), against the N before
    Nm and Nw need their own length and the length before it in the rows - never a button with partial figures. One
    that does not fit is left out: why 'window' (longer than the window) or 'before' (the period before not in the rows)."""
    o = opts or {}
    names = [str(x).strip().lower() for x in name_list(o.get("periods") or list(PERIODS))]
    wk = str(o.get("week_start") or "mon").strip().lower()[:3]
    if wk not in WEEKDAYS:
        raise ValueError("servicenow_insights_week_start: %s is not a day (mon ... sun)" % o.get("week_start"))
    ws = WEEKDAYS.index(wk)
    end = until.astimezone(tz)
    last = (until - datetime.timedelta(seconds=1)).astimezone(tz).date()
    at_mid = end.time() == datetime.time(0)
    out, left = [], []
    for nm in names:
        kind, k, label = period_name(nm)
        if nm == "all":
            a, b, ca, cb = since, until, prev, since
        elif nm == "this_month":
            a, b = _midnight(_month(last), tz), until
            ca = _midnight(_month(last, -1), tz)
            cb = min((ca.replace(tzinfo=None) + (end.replace(tzinfo=None) - a.replace(tzinfo=None))).replace(tzinfo=tz), a)
        elif kind == "m":
            bd = end.date() if at_mid and end.day == 1 else _month(last)
            b = _midnight(bd, tz)
            a, ca, cb = _midnight(_month(bd, -k), tz), _midnight(_month(bd, -2 * k), tz), _midnight(_month(bd, -k), tz)
        else:
            d = end.date()
            x = d - datetime.timedelta(days=(d.weekday() - ws) % 7)
            b = _midnight(x, tz)
            a, ca, cb = _midnight(x - datetime.timedelta(days=7 * k), tz), _midnight(x - datetime.timedelta(days=14 * k), tz), \
                _midnight(x - datetime.timedelta(days=7 * k), tz)
        if any(x["name"] == nm for x in out + left):
            continue
        if a < since or b > until or b <= a:
            if kind:
                left.append({"name": nm, "why": "window"})
            continue
        if ca is None or ca < prev or cb <= ca:
            if kind:
                left.append({"name": nm, "why": "before"})
                continue
            ca = cb = None
        out.append({"name": nm, "label": label, "kind": kind, "n": k, "a": a, "b": b, "ca": ca, "cb": cb, "title": period_title(a, b, tz),
                    "ctitle": period_title(ca, cb, tz) if ca else ""})
    # a report that ends with its month: this month is last month - one button
    lm = [(p["a"], p["b"]) for p in out if p["name"] == "1m"]
    return [p for p in out if not (p["name"] == "this_month" and (p["a"], p["b"]) in lm)], left


def periods_left_line(left, why_before=""):
    """The muted line under the period buttons (1.16.0): what was left out and why - '9 and 12 months need a longer window:
    servicenow_insights_months.' why_before: what keeps the period before out of the rows."""
    def names(xs):
        out = []
        for unit, kind in (("month", "m"), ("week", "w")):
            ns = [int(x["name"][:-1]) for x in xs if x["name"].endswith(kind)]
            if ns:
                ns = sorted(set(ns))
                out.append("%s %s%s" % (" and ".join([", ".join(str(n) for n in ns[:-1]), str(ns[-1])]) if len(ns) > 1 else str(ns[0]),
                                        unit, "" if ns == [1] else "s"))
        return " and ".join(out)
    parts = []
    w = [x for x in left or [] if x["why"] == "window"]
    b = [x for x in left or [] if x["why"] == "before"]
    if w:
        parts.append("%s need%s a longer window: servicenow_insights_months." % (names(w), "s" if names(w) in ("1 month", "1 week") else ""))
    if b:
        parts.append("%s need%s the period before as well: %s." % (names(b), "s" if names(b) in ("1 month", "1 week") else "",
                                                                    why_before or "the window before is not in this page"))
    return " ".join(parts)


def _bd_ord(d, days, hol):
    """Business days from 0001-01-01 up to and including d: the weekdays in days, less the holidays (sorted ISO dates)."""
    k = d.toordinal()                       # 1 = Monday 0001-01-01
    w, r = divmod(k, 7)
    n = w * len(days) + sum(1 for i in range(r) if i in days)
    return n - bisect.bisect_right(hol, d.isoformat())


def day_calendar(start, until, since, spec, tz):
    """The local days from start's to until's: each day's start in seconds from since, and its business-day count (the
    business days from the first day up to it: the age in business days = the end day's less the opened day's - as
    business_days)."""
    days, hol = (spec[0], spec[3]) if spec else (set(range(5)), set())
    hl = sorted(h for h in hol if datetime.date.fromisoformat(h).weekday() in days)
    d, lastd = start.astimezone(tz).date(), until.astimezone(tz).date()
    base = _bd_ord(d, days, hl)
    mid, bo = [], []
    while d <= lastd:
        mid.append(int((_midnight(d, tz) - since).total_seconds()))
        bo.append(_bd_ord(d, days, hl) - base)
        d += datetime.timedelta(days=1)
    return {"d0": start.astimezone(tz).date().isoformat(), "mid": mid, "bo": bo, "base": base, "days": sorted(days), "hol": hl}


def servicenow_insights_hierarchy(names, opts=None, tree=None, tree_ok=True):
    """Every group's place (hier_place; its ServiceNow parents from tree, the groups as the module read them) ->
    {group: {site, dir, team, rule, by, under}}."""
    o = opts or {}
    if org_is_map(o):
        return org_hierarchy(names, o)
    roots = group_roots(tree)
    out = {}
    for n in names or []:
        if str(n or "").strip():
            h = hier_place(n, o, group_chain(n, tree) if tree else None, roots, tree_ok)
            h["by"] = PLACED_BY.get(h["rule"], h["rule"])
            out[str(n)] = h
    return out


def _level_key(name):
    """A to Z, Other last (sites and directorates)."""
    return (str(name) == OTHER,) + tuple(_nat(name))


def _nat(name):
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", " ".join(str(name or "").split()).lower()) if p]


def _weeks(since, until, tz):
    """The whole weeks (Monday to Sunday, in the report's zone) inside the window: a part-week at either end would read as
    a dip. A window shorter than two weeks keeps its part-weeks."""
    start = since.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    first = start + datetime.timedelta(days=(7 - start.weekday()) % 7)
    out = []
    w = first
    while (w + datetime.timedelta(days=7)).astimezone(UTC) <= until:
        out.append(w.astimezone(UTC))
        w += datetime.timedelta(days=7)
    if len(out) < 2:
        out, w = [], start - datetime.timedelta(days=start.weekday())
        while w.astimezone(UTC) < until:
            out.append(w.astimezone(UTC))
            w += datetime.timedelta(days=7)
    return out


def _movement(rows, limit, rising_bad=True):
    """rows [{key, cur, prev}] -> each with rank and mv {kind, n, text}: rank against the window before."""
    cur = sorted(rows, key=lambda x: (-x["cur"], x["key"]))
    prev = sorted(rows, key=lambda x: (-x["prev"], x["key"]))
    pidx = dict((x["key"], i) for i, x in enumerate(prev) if x["prev"] > 0)
    for i, x in enumerate(cur):
        x["rank"] = i + 1
        p = pidx.get(x["key"])
        if p is None or p >= limit:
            x["mv"] = {"kind": "new", "n": 0, "text": "New"}
        else:
            d = p - i
            if d == 0:
                x["mv"] = {"kind": "same", "n": 0, "text": "—"}
            else:
                kind = ("up" if d > 0 else "down") if rising_bad else ("n-up" if d > 0 else "n-down")
                x["mv"] = {"kind": kind, "n": abs(d), "text": ("▲ " if d > 0 else "▼ ") + str(abs(d)), "was": p + 1}
    return cur


def _quality(notes, opts):
    c = {"good": 0, "thin": 0, "bare": 0}
    for n in notes:
        c[grade(n, opts)] += 1
    t = sum(c.values())
    if not t:
        return [0, 0, 0], 0
    g, th = round(100.0 * c["good"] / t), round(100.0 * c["thin"] / t)
    return [g, th, max(0, 100 - g - th)], t


def _r1(x):
    return None if x is None else round(x, 1)


def _top(counter, n=3):
    return [k for k, _ in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _count(d, k, n=1):
    d[k] = d.get(k, 0) + n


def ticket_prefixes(setting):
    """servicenow_insights_ticket_prefixes (1.30.0) -> ({PREFIX: table}, the entries refused): a prefix of letters only (any
    case), a table name of a-z, 0-9 and _ only."""
    good, bad = {}, []
    for k, v in sorted((setting or {}).items(), key=lambda kv: str(kv[0])) if isinstance(setting, dict) else []:
        if re.match(r"^[A-Za-z]+$", str(k)) and re.match(r"^[a-z0-9_]+$", str(v or "")):
            good[str(k).upper()] = str(v)
        else:
            bad.append("%s: %s" % (k, v))
    if setting and not isinstance(setting, dict):
        bad.append(str(setting))
    return good, bad


def servicenow_insights_vip_errors(fields, people="caller"):
    """1.43.0: what is wrong with servicenow_insights_vip_fields / vip_people -> [text] ([] = fine)."""
    out = []
    if not isinstance(fields, list):
        return ["servicenow_insights_vip_fields is a list of {label, column}"]
    for f in fields:
        c = str((f or {}).get("column") or "").strip() if isinstance(f, dict) else ""
        if not re.match(r"^[a-z0-9_]{1,80}$", c):
            out.append("%s: column is a sys_user column name, a-z, 0-9 and _ only" % (f if not isinstance(f, dict) else (f.get("label") or c or "(no column)")))
    if str(people or "").strip().lower() not in ("caller", "caller_and_opened_by"):
        out.append("servicenow_insights_vip_people: caller or caller_and_opened_by, not %s" % people)
    return out


def servicenow_insights_prefix_errors(setting):
    """The servicenow_insights_ticket_prefixes entries refused (1.30.0): [] = all good."""
    return ticket_prefixes(setting)[1]


def prefix_map(items, opts=None):
    """{prefix: table} for the ticket links (1.30.0): learned from the records read (an instance may number its incidents
    other than INC), then servicenow_insights_ticket_prefixes for a prefix no record read carried."""
    out = {}
    for it in items or []:
        m = re.match(r"^([A-Z]+)\d+$", str(it.get("number") or ""))
        if m and it.get("table"):
            out.setdefault(m.group(1), it["table"])
    for k, v in ticket_prefixes((opts or {}).get("ticket_prefixes"))[0].items():
        out.setdefault(k, v)
    return out


def servicenow_insights_model(r, state=None, mask=None, opts=None):
    """Everything the report shows, for the site (scopes.all) and each team (scopes[team]) - see docs/SERVICENOW_INSIGHTS.md."""
    o = opts or {}
    r = r or {}
    state = state or servicenow_insights_state()
    mask = mask or {"pairs": [], "counts": {}}
    win = r.get("window") or {}
    tz = _tz(win.get("time_zone"))
    since, until, prev = _window(r)
    prev = prev or since
    items = [dict(x) for x in r.get("items") or []]           # the model marks them: never the facts themselves
    issues_of = _assign(r, state, mask, o)
    issue_def = dict((x["name"], x) for x in state.get("issues") or [])
    act_ran = int(state.get("answered") or 0) > 0 and bool(state.get("classes"))
    fixed = o.get("_fixed")                   # a split's half (0.97.0): the whole site's teams and places, its tickets only
    teams = list(fixed["teams"]) if fixed else (org_teams(r, o) if org_is_map(o) else servicenow_insights_teams(r, o))
    repeat = datetime.timedelta(days=float(_o(o, "repeat_days", 14)))
    old_days = float(_o(o, "old_days", 30))
    weeks = _weeks(since, until, tz)

    sla_src = apply_slas(items, r, o, tz)
    vip = vip_tiers(r, o)
    for it in items:
        it["_vip"] = vip_of(it, vip) if vip["on"] and it.get("table") in ("incident", "sc_req_item") else None
    sla_r = r.get("sla") or {}
    resp_measured = sla_src == "task_sla" or (sla_src == "targets" and bool(r.get("first_assigned")) and not r.get("first_assigned_error")
                                              and any(isinstance(v, dict) and not _none(v.get("response"))
                                                      for t_ in ((o.get("sla_targets") or {}).values()) if isinstance(t_, dict) for v in t_.values()))
    cat_list = [" ".join(str(c).split()) for c in name_list(o.get("categories") or []) if str(c).strip()]
    cat_known = dict((c.lower(), c) for c in cat_list)
    # 1.7.0: the process checks - the 3-hop rule, on hold, the resolution codes, closure, the surveys
    hop_limit = int(_o(o, "hop_limit", 3))
    bh_spec = business_hours_spec(o.get("business_hours"))
    states = r.get("states") or {}
    states_read = bool(states) and not r.get("states_error")
    paused = (r.get("sla") or {}).get("paused") or {}
    caller_days = float(_o(o, "awaiting_caller_days", 6))
    close_after = float(_o(o, "close_after_days", 7))
    codes = [str(c).strip() for c in name_list(o.get("scrutiny_codes") or ["Duplicate", "Known Error", "No Resolution Provided"]) if str(c).strip()]
    review_code = str(o.get("review_code") or "No Resolution Provided").strip().lower()
    survey_rows = [x for x in r.get("survey") or [] if isinstance(x, (list, tuple)) and len(x) >= 4]
    survey_plan = survey_questions(survey_rows, r.get("survey_questions") or {}, o, since, until)
    risk_pct = float(_o(o, "sla_risk_pct", 75))
    open_slas = (r.get("sla") or {}).get("open") or {}
    omap = dict((" ".join(str(k).split()).lower(), str(v)) for k, v in (o.get("outcome_map") or OUTCOME_MAP).items())
    unable = [str(x) for x in name_list(o.get("unable_outcomes") or ["Known Error", "No Resolution Provided"])]
    desk = set(name_list(o.get("service_desk_groups") or []))
    # 1.9.0: the life cycle and the flow
    core_spec = business_hours_spec(o.get("core_hours") or o.get("business_hours") or CORE_HOURS)
    short_chars = int(_o(o, "short_desc_chars", 15))
    hold_many = int(_o(o, "hold_many", 2))
    repeat_ci = datetime.timedelta(days=float(_o(o, "repeat_ci_days", 7)))
    person_metrics = bool(o.get("person_metrics"))
    by_table = {}
    for it in items:
        by_table.setdefault(it.get("table"), []).append(it)
    inc_all = by_table.get("incident", [])
    # the groups' members (sys_user_grmember), and where each person works: the group whose tickets they hold or resolve
    # most, else a group they are a member of (the "From" of who sends a team its tickets)
    members = dict((g, [str(x) for x in v]) for g, v in (r.get("members") or {}).items() if isinstance(v, list))
    members_known = bool(members) and not r.get("members_error")
    work = {}
    for it in items:
        for k in ("assigned_to", "resolved_by", "closed_by"):
            who = str(it.get(k) or "").strip()
            if who and it.get("group"):
                _count(work.setdefault(who, {}), it["group"])
    home = dict((who, _top(g, 1)[0]) for who, g in work.items())
    for g in sorted(members):
        for who in members[g]:
            home.setdefault(who, g)
    group_names = set(it.get("group") for it in items if it.get("group")) | set(members) | set(teams)
    for ev in (r.get("audit") or {}).values():
        for e_ in ev or []:
            group_names.update(x for x in e_[1:] if x)
    for it in inc_all:
        it["_hold_now"] = "hold" in str(it.get("state") or "").lower() and not _t(it.get("done_at")) and it.get("active") is not False
        it["_hold_since"], it["_hold_src"] = None, ""
        if it["_hold_now"]:
            st = [x for x in states.get(it["sys_id"]) or [] if "hold" in str(x[0]).lower()]
            if st and _t(st[-1][1]):
                it["_hold_since"], it["_hold_src"] = _t(st[-1][1]), "metric"
            elif _t(paused.get(it["sys_id"])):
                it["_hold_since"], it["_hold_src"] = _t(paused[it["sys_id"]]), "task_sla"
        held_h = [(_t(x[2]) or until) - _t(x[1]) for x in states.get(it["sys_id"]) or [] if "hold" in str(x[0]).lower() and _t(x[1])]
        it["_hold_h"] = (sum(d.total_seconds() for d in held_h) / 3600.0) if states_read and it["sys_id"] in states else None
        it["_hops"] = it.get("reassignment_count") or 0
        # time in each state (the state metric): hours per kind, the times put on hold
        it["_tis"], it["_holds"] = None, None
        if states_read and it["sys_id"] in states:
            tis = {}
            for v_, a_, b_ in states[it["sys_id"]]:
                k_ = state_kind(v_)
                if k_ and _t(a_):
                    tis[k_] = tis.get(k_, 0.0) + max(0.0, ((_t(b_) or until) - _t(a_)).total_seconds() / 3600.0)
            it["_tis"] = tis
            it["_holds"] = sum(1 for x in states[it["sys_id"]] if state_kind(x[0]) == "hold")
        it["_outcome"] = omap.get(" ".join(str(it.get("close_code") or "").split()).lower(), "Fixed") if it.get("done_at") else ""
        st_ = states.get(it["sys_id"]) or []
        res_t = [_t(x[1]) for x in st_ if str(x[0]).lower() == "resolved" and _t(x[1])]
        it["_reopen7"] = (any(_t(y[1]) and t_ < _t(y[1]) <= t_ + datetime.timedelta(days=7) and str(y[0]).lower() not in ("resolved", "closed", "canceled", "cancelled")
                              for t_ in res_t for y in st_)) if states_read and it["sys_id"] in states else None
        it["_risk"] = None
        run_ = [x for x in open_slas.get(it["sys_id"]) or [] if x.get("running")]
        if run_ and not _t(it.get("done_at")):
            w = max(run_, key=lambda x: (bool(x.get("breached")), x.get("percent") or 0))
            if w.get("breached") or (w.get("percent") or 0) >= risk_pct:
                left = (w["left_s"] / 3600.0) if w.get("left_s") is not None else (
                    business_hours(until, _t(w.get("planned_end")), bh_spec, tz) if _t(w.get("planned_end")) and _t(w["planned_end"]) > until else None)
                it["_risk"] = {"sla": w.get("name") or "", "pct": w.get("percent"), "breached": bool(w.get("breached")), "left_h": 0.0 if w.get("breached") else left}
        it["_issue"] = issues_of.get(it["sys_id"])
        it["_grade"] = grade(it.get("close_notes"), o) if it.get("done_at") else ""
    for it in by_table.get("sc_task", []):
        it["_grade"] = grade(it.get("close_notes"), o) if it.get("done_at") else ""

    # came back: a new incident from the same caller with the same issue within repeat_days of this one's resolution
    by_caller = {}
    for it in inc_all:
        if it.get("caller_id") or it.get("caller"):
            by_caller.setdefault((it.get("caller_id") or it.get("caller"), it["_issue"]), []).append(it)
    for lst in by_caller.values():
        lst.sort(key=lambda x: x.get("opened_at") or "")
        for i, it in enumerate(lst):
            d = _t(it.get("done_at"))
            it["_came_back"] = bool(d and any(d < _t(y.get("opened_at")) <= d + repeat for y in lst[i + 1:] if _t(y.get("opened_at"))))
    inc_win = [x for x in inc_all if _in(_t(x.get("opened_at")), since, until)]
    reopened = sum(1 for x in inc_win if (x.get("reopen_count") or 0) > 0)
    reopen_used = bool(inc_win) and 100.0 * reopened / len(inc_win) >= float(_o(o, "reopen_min_percent", 0.2))

    # problem candidates (whole site; each team takes its own)
    pmin, pdays = int(_o(o, "problem_min", 3)), float(_o(o, "problem_days", 30))
    problems = dict((p.get("number"), p) for p in by_table.get("problem", []))
    cands = []
    groups_ci = {}
    for it in inc_win:
        if it.get("cmdb_ci"):
            groups_ci.setdefault((it["_issue"], it["cmdb_ci"]), []).append(it)
    for (iss, ci), lst in groups_ci.items():
        if len(lst) < pmin:
            continue
        ts = sorted(_t(x.get("opened_at")) for x in lst)
        best, j = 0, 0
        for i in range(len(ts)):
            while ts[i] - ts[j] > datetime.timedelta(days=pdays):
                j += 1
            best = max(best, i - j + 1)
        if best < pmin:
            continue
        recs = {}
        for x in lst:
            if x.get("problem"):
                _count(recs, x["problem"])
        rec = _top(recs, 1)[0] if recs else ""
        if not rec:
            rec = next((p.get("number") for p in by_table.get("problem", []) if p.get("cmdb_ci") == ci and p.get("active")), "")
        p = problems.get(rec) or {}
        pstate = p.get("state") or ("" if not rec else "")
        cont = bool(p.get("done_at") and any(_t(x.get("opened_at")) and _t(x["opened_at"]) > _t(p["done_at"]) for x in lst))
        gs = {}
        for x in lst:
            _count(gs, x.get("group") or "")
        last = sum(1 for t in ts if t >= until - datetime.timedelta(days=pdays))
        cands.append({"issue": iss, "ci": ci, "count": len(lst), "last": last, "group": _top(gs, 1)[0], "groups": list(gs),
                      "problem": rec, "problem_state": pstate, "continues": cont})
    cands.sort(key=lambda c: (-c["last"], -c["count"], c["issue"]))
    # tagged on the issue when a candidate needs a problem record (none, or resolved while the incidents go on)
    cand_issues = set(c["issue"] for c in cands if not c["problem"] or c["continues"])

    # hand-offs: the paths from the assignment history
    hmin = int(_o(o, "handoff_min", 3))
    audit = r.get("audit") or {}
    paths = {}
    straight = {}
    for it in inc_win:
        if (it.get("reassignment_count") or 0) == 0:
            straight.setdefault(it["_issue"], []).append(_hours(it))
    for it in inc_win:
        if (it.get("reassignment_count") or 0) < hmin:
            continue
        ev = audit.get(it["sys_id"]) or []
        if not ev:
            continue
        path = [ev[0][1] or ev[0][2]] + [e[2] for e in ev]
        path = [g for i, g in enumerate(path) if g and (i == 0 or g != path[i - 1])]
        if len(path) < 2:
            continue
        key = tuple(path)
        e = paths.setdefault(key, {"path": list(path), "count": 0, "issues": {}, "hours": [], "items": []})
        e["count"] += 1
        _count(e["issues"], it["_issue"])
        e["hours"].append(_hours(it))
        e["items"].append(it.get("number"))
    for e in paths.values():
        top = _top(e["issues"], 1)[0]
        a, b = _median(e["hours"]), _median(straight.get(top) or [])
        e["issue"] = top
        e["added_days"] = round((a - b) / 24.0, 1) if (a is not None and b is not None) else None
        e["items"] = e["items"][:10]
        del e["hours"], e["issues"]
    path_rows = sorted(paths.values(), key=lambda e: (-e["count"], e["path"]))

    # changes and the incidents that followed them
    fhours = datetime.timedelta(hours=float(_o(o, "change_follow_hours", 72)))
    failed_codes = set(str(c).strip().lower() for c in (o.get("change_failed_codes") or ["unsuccessful"]))
    backout = [str(w).strip().lower() for w in (o.get("change_backout_words") or []) if str(w).strip()]
    by_ci = {}
    for it in inc_all:
        if it.get("cmdb_ci"):
            by_ci.setdefault(it["cmdb_ci"], []).append(it)
    caused = {}
    for it in inc_all:
        if it.get("caused_by"):
            caused.setdefault(it["caused_by"], []).append(it)
    chg_win = [c for c in by_table.get("change_request", []) if _in(_t(c.get("closed_at") or c.get("done_at")), since, until)]
    # change tasks (1.2.0): each change's tasks, by its number (the reference as shown) or its sys_id; held up by = the
    # group of the task closed last, the wait from when every other task was closed (one task: from the change's work
    # start) to that close
    ct_all = by_table.get("change_task", [])
    ct_read = bool(ct_all) or any(t.get("table") == "change_task" and not t.get("error") for t in r.get("tables") or [])
    tasks_of = {}
    for x in ct_all:
        for k_ in set([x.get("parent"), x.get("parent_id")]):
            if k_:
                tasks_of.setdefault(k_, []).append(x)
    for c in chg_win:
        tl = tasks_of.get(c.get("number")) or tasks_of.get(c.get("sys_id")) or []
        c["_tasks"] = len(tl)
        done_ = sorted([x for x in tl if _t(x.get("done_at"))], key=lambda x: _t(x["done_at"]))
        c["_held"] = None
        if done_:
            last = _t(done_[-1]["done_at"])
            start = _t(done_[-2]["done_at"]) if len(done_) > 1 else _t(c.get("start_date"))
            if start and last >= start:
                c["_held"] = [done_[-1].get("group") or "(no group)", (last - start).total_seconds() / 3600.0]
    for c in chg_win:
        code = str(c.get("close_code") or "").strip().lower()
        text = (code + " " + str(c.get("close_notes") or "")).lower()
        c["_outcome"] = "Failed" if code in failed_codes else ("Backed out" if any(w in text for w in backout) else "Successful")
        c["_emergency"] = "emergency" in (str(c.get("change_type_value") or "") + " " + str(c.get("change_type") or "")).lower()
        fol = [[x.get("number"), "link"] for x in caused.get(c.get("number"), [])]
        end = _t(c.get("end_date")) or _t(c.get("closed_at"))
        if end and c.get("cmdb_ci"):
            for x in by_ci.get(c["cmdb_ci"], []):
                ot = _t(x.get("opened_at"))
                if ot and end < ot <= end + fhours and x.get("number") not in [f[0] for f in fol]:
                    fol.append([x.get("number"), "ci"])
        c["_followed"] = fol

    def scope_model(team, groups=None, level=None, key=None, name=None):
        """One scope: the site (team None, groups None), one team, or a site or directorate (groups: its assignment
        groups). Each ticket is one record (its group the one it ended in), so it counts once at every level."""
        allscope = team is None and groups is None
        gs = set(groups) if groups is not None else ({team} if team is not None else set())

        def mine(it):
            return allscope or (it.get("group") or "") in gs

        inc = [x for x in inc_all if mine(x)]
        inc_now = [x for x in inc if _in(_t(x.get("opened_at")), since, until)]
        inc_prev = [x for x in inc if _in(_t(x.get("opened_at")), prev, since)]
        res_now = [x for x in inc if _in(_t(x.get("done_at")), since, until)]
        res_prev = [x for x in inc if _in(_t(x.get("done_at")), prev, since)]
        ritm = [x for x in by_table.get("sc_req_item", []) if mine(x)]
        ritm_now = [x for x in ritm if _in(_t(x.get("opened_at")), since, until)]
        tasks = [x for x in by_table.get("sc_task", []) if mine(x)]
        flow = inc + ritm
        sla_now = [x for x in flow if _in(_t(x.get("done_at")), since, until) and x.get("made_sla") is not None]
        sla_prev = [x for x in flow if _in(_t(x.get("done_at")), prev, since) and x.get("made_sla") is not None]
        resp_now = [x for x in flow if _in(_t(x.get("done_at")), since, until) and x.get("_resp") is not None]
        resp_prev = [x for x in flow if _in(_t(x.get("done_at")), prev, since) and x.get("_resp") is not None]
        done_now = [x for x in flow if _in(_t(x.get("done_at")), since, until)]
        notes_now = [x.get("close_notes") for x in res_now] + [x.get("close_notes") for x in tasks if _in(_t(x.get("done_at")), since, until)]
        notes_prev = [x.get("close_notes") for x in res_prev] + [x.get("close_notes") for x in tasks if _in(_t(x.get("done_at")), prev, since)]
        q_now, q_n = _quality(notes_now, o)
        q_prev, _ = _quality(notes_prev, o)

        def backlog_at(t):
            return sum(1 for x in flow if _t(x.get("opened_at")) and _t(x["opened_at"]) <= t and (not _t(x.get("done_at")) or _t(x["done_at"]) > t))

        wk = []
        for ws in weeks:
            we = min(ws + datetime.timedelta(days=7), until)
            wk.append({"start": ws.astimezone(tz).strftime("%Y-%m-%d"),
                       "opened": sum(1 for x in flow if _in(_t(x.get("opened_at")), max(ws, since), we)),
                       "resolved": sum(1 for x in flow if _in(_t(x.get("done_at")), max(ws, since), we)),
                       "backlog": backlog_at(we)})
        bl_now, bl_start = backlog_at(until), backlog_at(since)
        open_now = [x for x in flow if _t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)]
        ages = [(until - _t(x["opened_at"])).total_seconds() / 86400.0 for x in open_now]
        age = [sum(1 for a in ages if a <= 5), sum(1 for a in ages if 5 < a <= 15), sum(1 for a in ages if 15 < a <= old_days),
               sum(1 for a in ages if a > old_days)]
        heat = [[0] * 24 for _ in range(7)]
        for x in inc_now:
            t = _t(x.get("opened_at")).astimezone(tz)
            heat[t.weekday()][t.hour] += 1

        # priority
        prio = {}
        for x in inc_now:
            k = x.get("priority_value") or "9"
            e = prio.setdefault(k, {"label": priority_label(k if k != "9" else "", x.get("priority"), o), "value": k, "count": 0, "hours": [],
                                    "sla": [0, 0], "resp": [0, 0], "no_slo": 0})
            e["count"] += 1
        for x in res_now:
            k = x.get("priority_value") or "9"
            if k in prio:
                prio[k]["hours"].append(_hours(x))
                for f_, kk in (("made_sla", "sla"), ("_resp", "resp")):
                    if x.get(f_) is not None:
                        prio[k][kk][1] += 1
                        prio[k][kk][0] += 1 if x[f_] else 0
                prio[k]["no_slo"] += 1 if x.get("_no_slo") else 0
        prio_rows = [{"label": e["label"], "value": e["value"], "count": e["count"], "median_h": _r1(_median(e["hours"])),
                      "sla": _r1(_pct(e["sla"][0], e["sla"][1])), "resp": _r1(_pct(e["resp"][0], e["resp"][1])), "no_slo": e["no_slo"],
                      "target_h": sla_target({"table": "incident", "priority_value": e["value"]}, o),
                      "resp_target_h": response_target({"table": "incident", "priority_value": e["value"]}, o)} for _, e in sorted(prio.items())]

        # the priority matrix (1.7.0): incidents whose priority is not the one their impact x urgency gives - set by hand
        mx = [(x, matrix_priority(x, o)) for x in inc_now]
        mx = [(x, p_) for x, p_ in mx if p_ and x.get("priority_value")]
        off = [(x, p_) for x, p_ in mx if str(x["priority_value"]) != p_]
        mg = {}
        for x, p_ in mx:
            e = mg.setdefault(x.get("group") or "(no group)", [0, 0])
            e[1] += 1
            e[0] += 1 if str(x["priority_value"]) != p_ else 0
        matrix = {"on": bool(o.get("priority_matrix", PRIORITY_MATRIX)), "checked": len(mx), "off": len(off), "pct": _r1(_pct(len(off), len(mx))),
                  "higher": sum(1 for x, p_ in off if str(x["priority_value"]) < p_), "lower": sum(1 for x, p_ in off if str(x["priority_value"]) > p_),
                  "by_group": sorted([{"group": g, "off": v[0], "of": v[1], "pct": _r1(_pct(v[0], v[1]))} for g, v in mg.items() if v[0]],
                                     key=lambda e: (-e["off"], -(e["pct"] or 0), e["group"]))[:5]}

        # categories (1.7.0): incidents per category, the time to resolve, met or missed; the official list's data quality
        cc, cp, ch, cg, cr, cs = {}, {}, {}, {}, {}, {}
        for x in inc_now:
            c_ = category_name(x)
            _count(cc, c_)
            _count(cg.setdefault(c_, {}), x.get("group") or "(no group)")
        for x in inc_prev:
            _count(cp, category_name(x))
        for x in res_now:
            c_ = category_name(x)
            ch.setdefault(c_, []).append(_hours(x))
            for f_, d_ in (("_resp", cr), ("made_sla", cs)):
                if x.get(f_) is not None:
                    e = d_.setdefault(c_, [0, 0])
                    e[1] += 1
                    e[0] += 1 if x[f_] else 0
        cat_rows = sorted([{"name": c_, "count": v, "prev": cp.get(c_, 0), "share": _r1(_pct(v, len(inc_now))), "median_h": _r1(_median(ch.get(c_, []))),
                            "resp": _r1(_pct(*cr[c_])) if c_ in cr else None, "resol": _r1(_pct(*cs[c_])) if c_ in cs else None,
                            "team": _top(cg.get(c_, {}), 1)[0], "official": (c_.lower() in cat_known) if cat_list else None}
                           for c_, v in cc.items()], key=lambda e: (-e["count"], e["name"]))
        empty_n = cc.get("(none)", 0)
        other_n = sum(v for c_, v in cc.items() if c_.lower() == "other")
        offl = sorted([[c_, v] for c_, v in cc.items() if cat_list and c_ != "(none)" and c_.lower() != "other" and c_.lower() not in cat_known],
                      key=lambda e: (-e[1], e[0]))
        categories = {"rows": cat_rows, "total": len(inc_now), "empty": empty_n, "empty_pct": _r1(_pct(empty_n, len(inc_now))),
                      "other": other_n, "other_pct": _r1(_pct(other_n, len(inc_now))), "listed": bool(cat_list),
                      "off_list": sum(v for _, v in offl), "off_pct": _r1(_pct(sum(v for _, v in offl), len(inc_now))), "off_names": offl[:30]}
        majors = sorted([x for x in inc_now if str(x.get("priority_value") or "") in ("1", "2")], key=lambda x: x.get("opened_at") or "")
        major_rows = [{"number": x.get("number"), "opened": _t(x["opened_at"]).astimezone(tz).strftime("%Y-%m-%d"),
                       "priority": "P" + str(x.get("priority_value")), "what": x.get("short_description"), "group": x.get("group"),
                       "hours": _r1(_hours(x)), "change": x.get("caused_by") or "", "problem": x.get("problem") or ""} for x in majors]

        # issues
        cur, prv, hours, ci, ex, grp, notes, wkly = {}, {}, {}, {}, {}, {}, {}, {}
        for x in inc_now:
            n = x["_issue"]
            _count(cur, n)
            _count(ci.setdefault(n, {}), x.get("cmdb_ci") or "") if x.get("cmdb_ci") else ci.setdefault(n, {})
            ex.setdefault(n, []).append(x)
            _count(grp.setdefault(n, {}), x.get("group") or "")
            t = _t(x["opened_at"])
            wi = sum(1 for w in weeks if w <= t) - 1
            if 0 <= wi < len(weeks) and t < weeks[wi] + datetime.timedelta(days=7):
                wkly.setdefault(n, [0] * len(weeks))[wi] += 1
        for x in inc_prev:
            _count(prv, x["_issue"])
        for x in res_now:
            hours.setdefault(x["_issue"], []).append(_hours(x))
            notes.setdefault(x["_issue"], []).append(x.get("close_notes"))
        rows = _movement([{"key": k, "cur": v, "prev": prv.get(k, 0)} for k, v in cur.items()], int(_o(o, "top_issues", 10)), True)
        total = len(inc_now)
        issue_rows, cum = [], 0
        for x in rows:
            n = x["key"]
            cum += x["cur"]
            q, _ = _quality(notes.get(n, []), o)
            fx = (state.get("fixes") or {}).get(n)
            if not fx:
                gnotes = {}
                for it in ex.get(n, []):
                    if it.get("_grade") == "good":
                        k = _norm(it.get("close_notes"))[:300]
                        e = gnotes.setdefault(k, [0, " ".join(str(it["close_notes"]).split())])
                        e[0] += 1
                tops = sorted(gnotes.values(), key=lambda e: -e[0])[:3]
                fx = {"steps": [e[1] for e in tops], "support": [e[0] for e in tops], "source": "tix" if tops else "none",
                      "why": ("The most common close notes that say what was done (%d of %d resolved tickets)." % (
                          sum(e[0] for e in tops), len(notes.get(n, []))) if tops else
                              "No close note of these tickets says what was done."), "disagree": False, "kb": None, "notes": True}
            d = issue_def.get(n) or {}
            tags = []
            if (x["cur"] >= int(_o(o, "kb_min", 50)) and fx.get("source") in ("tix", "mix") and not fx.get("disagree")) or fx.get("workaround"):
                tags.append("kb")                                          # a repeated workaround is a candidate too (0.96.0)
            if d.get("self_service"):
                tags.append("ss")
            if n in cand_issues:
                tags.append("pb")
            g = grp.get(n, {})
            main = _top(g, 1)[0] if g else ""
            newest = sorted(ex.get(n, []), key=lambda it: it.get("opened_at") or "", reverse=True)[:3]
            issue_rows.append({"name": n, "count": x["cur"], "prev": x["prev"], "rank": x["rank"], "mv": x["mv"],
                               "share": _r1(_pct(x["cur"], total)), "cum": round(_pct(cum, total) or 0),
                               "median_h": _r1(_median(hours.get(n, []))), "quality": q, "fix": fx, "tags": tags,
                               "cis": _top(ci.get(n, {}), 3), "examples": [it.get("number") for it in newest],
                               "main_group": main, "main_share": round(_pct(g.get(main, 0), x["cur"]) or 0) if main else 0,
                               "weekly": wkly.get(n, [0] * len(weeks)), "kind": d.get("kind") or "incident",
                               # 1.17.0: the fix question's sentence first - the same with the ACT cache (which keeps no "about")
                               "about": (fx or {}).get("about") or d.get("about") or ""})
        misfiled = [r_ for r_ in issue_rows if r_["kind"] == "request"]
        if not act_ran:
            misfiled = []

        # requests
        rc, rp, rdays, rsla, rw = {}, {}, {}, {}, {}
        for x in ritm_now:
            n = x.get("cat_item") or "(no catalog item)"
            _count(rc, n)
            t = _t(x["opened_at"])
            wi = sum(1 for w in weeks if w <= t) - 1
            if 0 <= wi < len(weeks) and t < weeks[wi] + datetime.timedelta(days=7):
                rw.setdefault(n, [0] * len(weeks))[wi] += 1
        for x in ritm:
            if _in(_t(x.get("opened_at")), prev, since):
                _count(rp, x.get("cat_item") or "(no catalog item)")
            if _in(_t(x.get("done_at")), since, until):
                n = x.get("cat_item") or "(no catalog item)"
                h = _hours(x)
                rdays.setdefault(n, []).append(None if h is None else h / 24.0)
                if x.get("made_sla") is not None:
                    e = rsla.setdefault(n, [0, 0])
                    e[1] += 1
                    e[0] += 1 if x["made_sla"] else 0
        req_rows = []
        for x in _movement([{"key": k, "cur": v, "prev": rp.get(k, 0)} for k, v in rc.items()], int(_o(o, "top_requests", 10)), False):
            n = x["key"]
            s = rsla.get(n)
            req_rows.append({"name": n, "count": x["cur"], "prev": x["prev"], "rank": x["rank"], "mv": x["mv"],
                             "median_days": _r1(_median(rdays.get(n, []))), "sla": _r1(_pct(s[0], s[1])) if s else None,
                             "weekly": rw.get(n, [0] * len(weeks))})

        # changes
        chg = [c for c in chg_win if mine(c)]
        failed_rows = [{"number": c.get("number"), "closed": _t(c.get("closed_at") or c.get("done_at")).astimezone(tz).strftime("%Y-%m-%d"),
                        "what": c.get("short_description"), "group": c.get("group"), "outcome": c["_outcome"], "followed": c["_followed"]}
                       for c in sorted(chg, key=lambda c: c.get("closed_at") or "") if c["_outcome"] != "Successful"]
        em = {}
        for c in chg_win:
            e = em.setdefault(c.get("group") or "(no group)", [0, 0])
            e[1] += 1
            e[0] += 1 if c["_emergency"] else 0
        em_rows = sorted([{"group": g, "emergency": v[0], "changes": v[1], "pct": _r1(_pct(v[0], v[1]))} for g, v in em.items()],
                         key=lambda e: (-e["changes"], e["group"]))[:8]
        changes = {"count": len(chg), "successful": sum(1 for c in chg if c["_outcome"] == "Successful"),
                   "failed": sum(1 for c in chg if c["_outcome"] == "Failed"), "backed_out": sum(1 for c in chg if c["_outcome"] == "Backed out"),
                   "followed": sum(1 for c in chg if c["_followed"]), "emergency": sum(1 for c in chg if c["_emergency"]),
                   "rows": failed_rows, "by_group": em_rows}
        if ct_read:
            with_t = [c for c in chg if c["_tasks"]]
            held = {}
            for c in chg:
                if c["_held"]:
                    held.setdefault(c["_held"][0], []).append(c["_held"][1])
            most = max(with_t, key=lambda c: (c["_tasks"], c.get("number") or "")) if with_t else None
            changes.update({"tasks": sum(c["_tasks"] for c in chg), "with_tasks": len(with_t),
                            "per_change": _r1(1.0 * sum(c["_tasks"] for c in with_t) / len(with_t)) if with_t else None,
                            "most": [most.get("number"), most["_tasks"]] if most else None,
                            "held_n": sum(len(v) for v in held.values()),
                            "held_by": sorted([{"group": g, "count": len(v), "median_h": _r1(_median(v))} for g, v in held.items()],
                                              key=lambda e: (-e["count"], -(e["median_h"] or 0), e["group"]))[:5]})

        # change tasks (1.2.0): opened, closed (by the planned end or after it), open now and how old, the time to close -
        # their own figures, not in the backlog, the weeks or the SLAs
        ctasks = None
        if ct_read:
            ct = [x for x in ct_all if mine(x)]
            ct_done = [x for x in ct if _in(_t(x.get("done_at")), since, until)]
            ct_open = [x for x in ct if _t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)]
            ct_ages = [(until - _t(x["opened_at"])).total_seconds() / 86400.0 for x in ct_open]
            ended = [x for x in ct_done if _t(x.get("planned_end"))]
            ctasks = {"opened": sum(1 for x in ct if _in(_t(x.get("opened_at")), since, until)), "closed": len(ct_done),
                      "closed_prev": sum(1 for x in ct if _in(_t(x.get("done_at")), prev, since)), "open": len(ct_open),
                      "age": [sum(1 for a in ct_ages if a <= 5), sum(1 for a in ct_ages if 5 < a <= 15),
                              sum(1 for a in ct_ages if 15 < a <= old_days), sum(1 for a in ct_ages if a > old_days)],
                      "on_time": sum(1 for x in ended if _t(x["done_at"]) <= _t(x["planned_end"])),
                      "overdue": sum(1 for x in ended if _t(x["done_at"]) > _t(x["planned_end"])),
                      "no_end": len(ct_done) - len(ended),
                      "late_open": sum(1 for x in ct_open if _t(x.get("planned_end")) and _t(x["planned_end"]) < until),
                      "median_h": _r1(_median([_hours(x) for x in ct_done]))}

        # close notes by group, the bare ones word for word
        bygrp = {}
        for x in [y for y in inc_all + by_table.get("sc_task", []) if _in(_t(y.get("done_at")), since, until)]:
            bygrp.setdefault(x.get("group") or "(no group)", []).append(x.get("close_notes"))
        q_groups = sorted([{"group": g, "quality": _quality(v, o)[0], "n": len(v)} for g, v in bygrp.items()],
                          key=lambda e: -e["n"])[:10]
        q_groups.sort(key=lambda e: -e["quality"][2])
        bare = {}
        for x in res_now + [t for t in tasks if _in(_t(t.get("done_at")), since, until)]:
            if grade(x.get("close_notes"), o) == "bare":
                k = " ".join(str(x.get("close_notes") or "").split()).lower() or "(no close note)"
                e = bare.setdefault(k, {"note": k, "count": 0, "groups": {}})
                e["count"] += 1
                _count(e["groups"], x.get("group") or "")
        bare_rows = [{"note": e["note"], "count": e["count"], "group": _top(e["groups"], 1)[0]} for e in
                     sorted(bare.values(), key=lambda e: (-e["count"], e["note"]))[:10]]

        # hand-offs
        h_now = [x for x in inc_now if (x.get("reassignment_count") or 0) >= hmin]
        hp = [e for e in path_rows if allscope or any(g in gs for g in e["path"])][:8]
        handoffs = {"count": len(h_now), "pct": _r1(_pct(len(h_now), len(inc_now))),
                    "median": _median([x.get("reassignment_count") or 0 for x in inc_now]), "paths": hp}

        # problem candidates of this scope
        pc = [c for c in cands if allscope or gs & set(c["groups"])]

        # repeat: reopened, came back
        rep_now = [x for x in res_now]
        reo = [_pct(sum(1 for x in lst if (x.get("reopen_count") or 0) > 0), len(lst)) for lst in (res_now, res_prev)]
        cb = [_pct(sum(1 for x in lst if x.get("_came_back")), len(lst)) for lst in (res_now, res_prev)]

        # ---- 1.7.0: what to act on first
        open_inc = [x for x in inc if _t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)]

        def prow_(x):
            return priority_label(x.get("priority_value"), x.get("priority"), o)

        def age_d(x):
            return _r1((until - _t(x["opened_at"])).total_seconds() / 86400.0)
        # A. the 3-hop rule: over = reassigned more than hop_limit + 1 times (the first assignment, then hop_limit transfers)
        over = lambda x: (x.get("_hops") or 0) > hop_limit + 1                     # noqa: E731
        hop_open = sorted([x for x in open_inc if over(x)], key=lambda x: x.get("opened_at") or "")
        hg, recv = {}, {}
        for x in res_now + open_inc:
            e = hg.setdefault(x.get("group") or "(no group)", [0, 0])
            e[1] += 1
            e[0] += 1 if over(x) else 0
            if over(x):
                ev = audit.get(x.get("sys_id")) or []
                for g in ([e_[2] for e_ in ev if e_[2]] if ev else [x.get("group") or "(no group)"]):
                    _count(recv, g)
        hops = {"limit": hop_limit, "done": sum(1 for x in res_now if over(x)), "done_of": len(res_now),
                "done_pct": _r1(_pct(sum(1 for x in res_now if over(x)), len(res_now))), "open": len(hop_open),
                "rows": [{"number": x.get("number"), "what": x.get("short_description"), "what_m": masked_excerpt(x.get("short_description"), mask, 100),
                          "hops": x.get("_hops"), "group": x.get("group"),
                          "age": age_d(x), "priority": prow_(x)} for x in hop_open[:50]],
                "by_group": sorted([{"group": g, "over": v[0], "of": v[1], "pct": _r1(_pct(v[0], v[1])), "received": recv.get(g, 0)}
                                    for g, v in hg.items() if v[0] or recv.get(g)], key=lambda e: (-e["received"], -e["over"], e["group"]))[:8],
                "most": _top(recv, 1)[0] if recv else "", "by_paths": bool(audit)}
        # B. on hold now: by reason and age (business days on hold), Awaiting Caller over the limit, no reason
        held = [x for x in open_inc if x.get("_hold_now")]
        hrows, hr = [], {}
        for x in sorted(held, key=lambda x: (x.get("_hold_since") or _t(x["opened_at"]))):
            bd = business_days(x["_hold_since"], until, bh_spec, tz) if x.get("_hold_since") else None
            reason = " ".join(str(x.get("hold_reason") or "").split()) or "(no reason)"
            late = bd is not None and "caller" in reason.lower() and bd > caller_days
            e = hr.setdefault(reason, [0, 0, 0, 0, 0])            # open, 0-2, 3-6, over 6 business days, not known
            e[0] += 1
            e[1 if bd is not None and bd <= 2 else (2 if bd is not None and bd <= caller_days else (3 if bd is not None else 4))] += 1
            hrows.append({"number": x.get("number"), "what": x.get("short_description"), "what_m": masked_excerpt(x.get("short_description"), mask, 100),
                          "reason": reason, "group": x.get("group"),
                          "since": x["_hold_since"].astimezone(tz).strftime("%Y-%m-%d") if x.get("_hold_since") else "",
                          "days": bd, "src": x.get("_hold_src") or "", "late": late, "priority": prow_(x), "age": age_d(x)})
        hgrp = {}
        for h in hrows:
            e = hgrp.setdefault(h["group"] or "(no group)", [0, 0, 0])
            e[0] += 1
            e[1] += 1 if h["reason"] == "(no reason)" else 0
            e[2] += 1 if h["late"] else 0
        hold_hours = [x["_hold_h"] for x in res_now if x.get("_hold_h") is not None]
        hold = {"open": len(held), "no_reason": sum(1 for h in hrows if h["reason"] == "(no reason)"), "late": [h for h in hrows if h["late"]][:50],
                "late_n": sum(1 for h in hrows if h["late"]), "by_reason": sorted([[k_] + v for k_, v in hr.items()], key=lambda e: -e[1]),
                "by_group": sorted([{"group": g, "open": v[0], "no_reason": v[1], "late": v[2]} for g, v in hgrp.items()], key=lambda e: (-e["open"], e["group"]))[:10],
                "unknown": sum(1 for h in hrows if h["days"] is None),
                "sources": sorted(set(h["src"] for h in hrows if h["src"])), "caller_days": caller_days,
                "median_h": _r1(_median(hold_hours)), "measured": states_read, "rows": hrows[:50]}
        # C. the resolution codes to look at, per team; No Resolution Provided one by one; reopened
        cg_ = {}
        for x in res_now:
            g = x.get("group") or "(no group)"
            e = cg_.setdefault(g, dict([(c_, 0) for c_ in codes] + [("_reopened", 0), ("_resolved", 0)]))
            e["_resolved"] += 1
            e["_reopened"] += 1 if (x.get("reopen_count") or 0) > 0 else 0
            code = " ".join(str(x.get("close_code") or "").split()).lower()
            for c_ in codes:
                if code == c_.lower():
                    e[c_] += 1
        nrp = [x for x in res_now if " ".join(str(x.get("close_code") or "").split()).lower() == review_code]
        cdx = {"codes": codes, "review": next((c_ for c_ in codes if c_.lower() == review_code), "No Resolution Provided"),
               "counts": dict((c_, sum(v[c_] for v in cg_.values())) for c_ in codes), "reopened": sum(v["_reopened"] for v in cg_.values()),
               "resolved": len(res_now),
               "by_group": sorted([dict(v, group=g) for g, v in cg_.items() if any(v[c_] for c_ in codes) or v["_reopened"]],
                                  key=lambda e: (-sum(e[c_] for c_ in codes) - e["_reopened"], e["group"]))[:10],
               "review_rows": [{"number": x.get("number"), "group": x.get("group"), "what": x.get("short_description"),
                                "what_m": masked_excerpt(x.get("short_description"), mask, 100),
                                "resolved": _t(x["done_at"]).astimezone(tz).strftime("%Y-%m-%d"),
                                "note": masked_excerpt(x.get("close_notes"), mask)}
                               for x in sorted(nrp, key=lambda x: x.get("done_at") or "", reverse=True)[:50]], "review_n": len(nrp)}
        # D. resolved, not closed after close_after_days (auto-close not working)
        stale = sorted([x for x in inc if x.get("resolved_at") and not x.get("closed_at") and _t(x["resolved_at"])
                        and (until - _t(x["resolved_at"])).total_seconds() > close_after * 86400], key=lambda x: x["resolved_at"])
        closure = {"days": close_after, "count": len(stale),
                   "rows": [{"number": x.get("number"), "group": x.get("group"), "resolved": _t(x["resolved_at"]).astimezone(tz).strftime("%Y-%m-%d"),
                             "days": _r1((until - _t(x["resolved_at"])).total_seconds() / 86400.0), "state": x.get("state")} for x in stale[:50]]}
        # 1.7.0 (2): SLA at risk now - open incidents whose running SLA is past sla_risk_pct, or breached and still open; worst first
        risk = sorted([x for x in open_inc if x.get("_risk")], key=lambda x: (not x["_risk"]["breached"], -(x["_risk"]["pct"] or 0), x.get("opened_at") or ""))
        rgrp = {}
        for x in risk:
            e = rgrp.setdefault(x.get("group") or "(no group)", [0, 0])
            e[0] += 1
            e[1] += 1 if x["_risk"]["breached"] else 0
        at_risk = {"pct": risk_pct, "count": len(risk), "breached": sum(1 for x in risk if x["_risk"]["breached"]),
                   "rows": [{"number": x.get("number"), "priority": prow_(x), "sla": x["_risk"]["sla"], "pct": x["_risk"]["pct"], "breached": x["_risk"]["breached"],
                             "left_h": _r1(x["_risk"]["left_h"]), "group": x.get("group"), "assignee": x.get("assigned_to") or ""} for x in risk[:50]],
                   "by_group": sorted([{"group": g, "count": v[0], "breached": v[1]} for g, v in rgrp.items()], key=lambda e: (-e["count"], e["group"]))[:10],
                   "read": bool(open_slas)}
        # the outcome mix: how the resolved incidents ended; unable to resolve = unable_outcomes / resolved
        def mix(lst):
            c_ = {}
            for x in lst:
                _count(c_, x.get("_outcome") or "Fixed")
            return c_
        mo_now, mo_prev = mix(res_now), mix(res_prev)
        ogrp = {}
        for x in res_now:
            _count(ogrp.setdefault(x.get("group") or "(no group)", {}), x.get("_outcome") or "Fixed")
        names_ = ["Fixed"] + [v for v in dict.fromkeys(omap.values()) if v != "Fixed"]
        un = lambda c_, n_: _r1(_pct(sum(c_.get(u, 0) for u in unable), n_))             # noqa: E731
        outcomes = {"names": names_, "now": mo_now, "prev": mo_prev, "unable": [un(mo_now, len(res_now)), un(mo_prev, len(res_prev))], "unable_names": unable,
                    "by_group": sorted([{"group": g, "resolved": sum(v.values()), "counts": v, "unable": un(v, sum(v.values()))} for g, v in ogrp.items()],
                                       key=lambda e: (-e["resolved"], e["group"]))[:12]}
        # reopened: at least once (reopen_count), and within 7 days of being resolved (the state metric, where read)
        r7 = [x for x in res_now if x.get("_reopen7") is not None]
        reopen7 = {"measured": bool(r7), "count": sum(1 for x in r7 if x["_reopen7"]), "of": len(r7), "pct": _r1(_pct(sum(1 for x in r7 if x["_reopen7"]), len(r7)))}
        # first-contact resolution: resolved by the first group it was assigned to (never reassigned); the service desk's own
        fcr_ = lambda lst: _r1(_pct(sum(1 for x in lst if (x.get("reassignment_count") or 0) == 0), len(lst)))     # noqa: E731
        sd_now, sd_prev = [x for x in res_now if x.get("group") in desk], [x for x in res_prev if x.get("group") in desk]
        fcr = {"now": fcr_(res_now), "prev": fcr_(res_prev), "desk": [fcr_(sd_now), fcr_(sd_prev)] if desk else None, "desk_n": len(sd_now),
               "desk_groups": sorted(desk)}

        # ---- 1.9.0: the life cycle, the flow, quality, hot spots, after hours, workload
        # time in each state (resolved in the period, the state metric): the average hours of each, per group - they add up
        tis_items = [x for x in res_now if x.get("_tis") is not None]
        tg = {}
        for x in tis_items:
            e = tg.setdefault(x.get("group") or "(no group)", {"n": 0, "new": 0.0, "progress": 0.0, "hold": 0.0, "resolved": 0.0})
            e["n"] += 1
            for k_ in STATE_KINDS:
                e[k_] += x["_tis"].get(k_, 0.0)
        tot = dict((k_, sum(x["_tis"].get(k_, 0.0) for x in tis_items)) for k_ in STATE_KINDS)
        life = tot["new"] + tot["progress"] + tot["hold"]           # the time to resolve (resolved to closed apart)
        hreason = {}
        for x in tis_items:
            if x["_tis"].get("hold"):
                r_ = " ".join(str(x.get("hold_reason") or "").split()) or "(reason not kept)"
                e = hreason.setdefault(r_, [0, 0.0])
                e[0] += 1
                e[1] += x["_tis"]["hold"]
        hold_hours = sum(v[1] for v in hreason.values())
        vendor_h = sum(v[1] for k_, v in hreason.items() if "vendor" in k_.lower())
        lifecycle = {"measured": bool(tis_items), "n": len(tis_items),
                     "avg": dict((k_, _r1(tot[k_] / len(tis_items)) if tis_items else None) for k_ in STATE_KINDS),
                     "by_group": sorted([dict([("group", g), ("n", v["n"])] + [(k_, _r1(v[k_] / v["n"])) for k_ in STATE_KINDS]) for g, v in tg.items()],
                                        key=lambda e: (-e["n"], e["group"]))[:12],
                     "hold_share": _r1(_pct(tot["hold"], life)), "held_many": sum(1 for x in tis_items if (x.get("_holds") or 0) > hold_many),
                     "hold_many": hold_many, "held_any": sum(1 for x in tis_items if (x.get("_holds") or 0) > 0),
                     "by_reason": sorted([[k_, v[0], _r1(v[1]), _r1(_pct(v[1], hold_hours))] for k_, v in hreason.items()], key=lambda e: -e[2]),
                     "vendor_share": _r1(_pct(vendor_h, hold_hours)),
                     "vendor_now": _r1(_pct(sum(1 for h in hold["rows"] if "vendor" in h["reason"].lower()), len(hold["rows"])))}
        # the backlog's age in business days, per group; the weekly flow (opened - resolved)
        open_flow = [x for x in flow if _t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)]
        ag = {}
        for x in open_flow:
            bd = business_days(_t(x["opened_at"]), until, bh_spec, tz) or 0
            e = ag.setdefault(x.get("group") or "(no group)", [0, 0, 0, 0])
            e[0 if bd <= 1 else (1 if bd <= 5 else (2 if bd <= 10 else 3))] += 1
        aging = {"buckets": ["0–1", "2–5", "6–10", "over 10"], "total": [sum(v[i] for v in ag.values()) for i in range(4)],
                 "by_group": sorted([{"group": g, "counts": v, "open": sum(v)} for g, v in ag.items()], key=lambda e: (-e["counts"][3], -e["open"], e["group"]))[:12],
                 "net": [w["opened"] - w["resolved"] for w in wk]}
        # ping-pong: a ticket sent back to the group it came from (A -> B -> A), from the paths
        pp, ppn = {}, 0
        for x in inc_now:
            ev = audit.get(x.get("sys_id")) or []
            seq = [g for g in ([ev[0][1] or ev[0][2]] + [e_[2] for e_ in ev] if ev else []) if g]
            seq = [g for i_, g in enumerate(seq) if i_ == 0 or g != seq[i_ - 1]]
            hit = False
            for i_ in range(len(seq) - 2):
                if seq[i_] == seq[i_ + 2]:
                    _count(pp, (seq[i_], seq[i_ + 1]))
                    hit = True
            ppn += 1 if hit else 0
        pingpong = {"count": ppn, "pct": _r1(_pct(ppn, len(inc_now))), "read": bool(audit),
                    "pairs": [[a_, b_, c_] for (a_, b_), c_ in sorted(pp.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]}
        # ticket quality: category Other or empty, no CI, a very short description - per group
        qg = {}
        for x in inc_now:
            e = qg.setdefault(x.get("group") or "(no group)", [0, 0, 0, 0])
            e[0] += 1
            e[1] += 1 if category_name(x).lower() in ("(none)", "other") else 0
            e[2] += 1 if not x.get("cmdb_ci") else 0
            e[3] += 1 if len(" ".join(str(x.get("short_description") or "").split())) < short_chars else 0
        tq = lambda v: {"n": v[0], "other": _r1(_pct(v[1], v[0])), "no_ci": _r1(_pct(v[2], v[0])), "short": _r1(_pct(v[3], v[0]))}   # noqa: E731
        quality_t = dict(tq([sum(v[i] for v in qg.values()) for i in range(4)]), short_chars=short_chars,
                         by_group=sorted([dict(tq(v), group=g) for g, v in qg.items()], key=lambda e: (-e["n"], e["group"]))[:12])
        # hot spots: the configuration items with the most incidents, and those back on the same CI within repeat_ci_days
        hc = {}
        for x in inc_now:
            if x.get("cmdb_ci"):
                hc.setdefault(x["cmdb_ci"], []).append(x)
        hot = []
        for ci_, lst in hc.items():
            ts = sorted(_t(x["opened_at"]) for x in lst)
            rep_ = sum(1 for i_ in range(1, len(ts)) if ts[i_] - ts[i_ - 1] <= repeat_ci)
            gs_ = {}
            for x in lst:
                _count(gs_, x.get("group") or "")
            hot.append({"ci": ci_, "count": len(lst), "repeats": rep_, "group": _top(gs_, 1)[0], "problem": sum(1 for x in lst if x.get("problem"))})
        hot.sort(key=lambda e: (-e["count"], -e["repeats"], e["ci"]))
        hotspots = {"cis": hot[:10], "repeat_days": repeat_ci.days, "with_problem": sum(1 for x in inc_now if x.get("problem")),
                    "problem_pct": _r1(_pct(sum(1 for x in inc_now if x.get("problem")), len(inc_now)))}
        # after hours: opened outside the core hours, per group
        ah = {}
        for x in inc_now:
            e = ah.setdefault(x.get("group") or "(no group)", [0, 0])
            e[0] += 1
            e[1] += 0 if in_hours(_t(x["opened_at"]), core_spec, tz) else 1
        after = {"pct": _r1(_pct(sum(v[1] for v in ah.values()), len(inc_now))), "count": sum(v[1] for v in ah.values()),
                 "core": business_hours_label(o.get("core_hours") or o.get("business_hours") or CORE_HOURS, win.get("time_zone")),
                 "by_group": sorted([{"group": g, "n": v[0], "after": v[1], "pct": _r1(_pct(v[1], v[0]))} for g, v in ah.items()],
                                    key=lambda e: (-(e["pct"] or 0), e["group"]))[:12]}
        # workload: open now and resolved in the period per person (the one it is assigned to); the median only with person_metrics
        wl = {}
        for x in open_flow:
            if x.get("assigned_to"):
                wl.setdefault(x["assigned_to"], {"open": 0, "resolved": 0, "h": [], "group": x.get("group") or ""})["open"] += 1
        for x in res_now:
            if x.get("assigned_to"):
                e = wl.setdefault(x["assigned_to"], {"open": 0, "resolved": 0, "h": [], "group": x.get("group") or ""})
                e["resolved"] += 1
                e["h"].append(_hours(x))
        workload = {"person_metrics": person_metrics, "unassigned": sum(1 for x in open_flow if not x.get("assigned_to")),
                    "rows": sorted([{"person": k_, "group": v["group"], "open": v["open"], "resolved": v["resolved"],
                                     "median_h": _r1(_median(v["h"])) if person_metrics else None} for k_, v in wl.items()],
                                   key=lambda e: (-e["open"], -e["resolved"], e["person"]))[:20]}

        # E. the surveys (1.16.0): each question on its own scale, by survey - never one average across scales; the
        # headline (a survey's overall on one scale, or one question) for the tile, the email and the history
        mine_ids = set(x["sys_id"] for x in inc)
        grp_of = dict((x["sys_id"], x.get("group") or "(no group)") for x in inc)
        survey = survey_scope(survey_plan, survey_rows, mine_ids, grp_of, since, until, prev, tz, o)

        lvl = level or ("all" if allscope else "team")
        sc = {"key": key or ("all" if allscope else team), "name": name or ("All assignment groups" if allscope else team),
              "groups_of": None if allscope else sorted(gs, key=_nat),      # the page's own rows for its periods (1.34.0)
              "slug": "all" if allscope else _slug(name or team), "team": team, "level": lvl,
              "counts": {"incidents": len(inc_now), "incidents_prev": len(inc_prev), "requests": len(ritm_now),
                         "changes": len(chg), "problems": sum(1 for p in by_table.get("problem", []) if mine(p) and
                                                              (_in(_t(p.get("opened_at")), since, until) or p.get("active"))),
                         "resolved": len(res_now)},
              "median_h": [_r1(_median([_hours(x) for x in res_now])), _r1(_median([_hours(x) for x in res_prev]))],
              "sla": [_r1(_pct(sum(1 for x in sla_now if x["made_sla"]), len(sla_now))), _r1(_pct(sum(1 for x in sla_prev if x["made_sla"]), len(sla_prev)))],
              "resp": [_r1(_pct(sum(1 for x in resp_now if x["_resp"]), len(resp_now))), _r1(_pct(sum(1 for x in resp_prev if x["_resp"]), len(resp_prev)))],
              "no_slo": [sum(1 for x in done_now if x.get("_no_slo")), len(done_now)], "matrix": matrix, "categories": categories,
              "hops": hops, "hold": hold, "codes": cdx, "closure": closure, "survey": survey, "at_risk": at_risk, "outcomes": outcomes,
              "reopen7": reopen7, "fcr": fcr, "lifecycle": lifecycle, "aging": aging, "pingpong": pingpong, "quality_t": quality_t,
              "hotspots": hotspots, "after": after, "workload": workload,
              "reopen": [_r1(reo[0]), _r1(reo[1])], "came_back": [_r1(cb[0]), _r1(cb[1])],
              "quality": q_now, "quality_prev": q_prev, "notes_graded": q_n,
              "backlog": [bl_now, bl_start], "age": age, "weeks": wk, "heat": heat,
              "prio": prio_rows, "majors": major_rows, "issues": issue_rows, "issue_total": len(cur), "misfiled": misfiled[:5],
              "requests": req_rows, "changes": changes, "notes_by_group": q_groups, "bare": bare_rows,
              "handoffs": handoffs, "problems": pc[:15], "resolved_n": len(rep_now), "ctasks": ctasks,
              "vip": vip_scope(inc_now, ritm_now, flow, since, until, vip, o)}
        if lvl == "team":
            sc["stars"] = stars(team)
        share = {"all": 1, "site": 0.6, "dir": 0.4}.get(lvl, 0.2)         # a draft's incidents, of kb_min, at this level
        sc["kb"] = [dict(fx_kb, issue=ir["name"], count=ir["count"], source=ir["fix"]["source"]) for ir in issue_rows
                    for fx_kb in [ir["fix"].get("kb")] if fx_kb and (ir["count"] >= int(_o(o, "kb_min", 50)) * share
                                                                     or ir["fix"].get("workaround"))]
        return sc

    def roster_of(team):
        """The team's people -> (names, known): its members in ServiceNow when sys_user_grmember was read, else everyone
        its tickets of the window were assigned to or resolved by."""
        if members_known:
            return set(members.get(team) or []), True
        names = set()
        for x in inc_all + by_table.get("sc_task", []) + by_table.get("sc_req_item", []) + ct_all:
            if (x.get("group") or "") == team and (_in(_t(x.get("opened_at")), since, until) or _in(_t(x.get("done_at")), since, until)):
                for k in ("assigned_to", "resolved_by", "closed_by"):
                    if x.get(k):
                        names.add(x[k])
        return names, False

    def senders(team, roster):
        """Who opens the team's tickets (Opened by, on the tickets assigned to it): ([{name, opened, from, member, end_user}],
        {tickets, people}) - the first 8, and of how many (1.18.0: the tickets in the period, the people who opened them)."""
        cnt, own, all_n = {}, {}, 0
        for x in inc_all + by_table.get("sc_req_item", []):
            if (x.get("group") or "") != team or not _in(_t(x.get("opened_at")), since, until):
                continue
            all_n += 1
            who = str(x.get("opened_by") or "").strip()
            if not who:
                continue
            _count(cnt, who)
            if who == str(x.get("caller") or "").strip():
                _count(own, who)
        rows = []
        for who, c in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
            member = who in roster
            end_user = (not member) and own.get(who, 0) * 2 > c and who not in work
            rows.append({"name": who, "opened": c, "from": team if member else ("" if end_user else home.get(who, "")),
                         "member": member, "end_user": end_user})
        return rows, {"tickets": all_n, "people": len(cnt)}

    def stars(team):
        weight = float(_o(o, "bare_weight", 0.5))
        # closed: incidents resolved, catalog tasks and change tasks (1.2.0) closed; the median to resolve without the
        # change tasks (their time is the change's plan)
        done = [x for x in inc_all + by_table.get("sc_task", []) + ct_all if (x.get("group") or "") == team and _in(_t(x.get("done_at")), since, until)]
        roster, known = roster_of(team)
        people = dict((who, {"name": who, "resolved": 0, "score": 0.0, "hours": [], "notes": [], "reopen": 0, "came_back": 0, "inc": 0, "opened": 0,
                             "ctask": 0}) for who in roster)
        outside = {}                 # resolved by someone outside the team, and not assigned to a member: counted, not ranked
        for x in done:
            who = (x.get("resolved_by") if x.get("table") == "incident" else x.get("closed_by")) or x.get("assigned_to") or ""
            if who not in people:
                if x.get("assigned_to") in people:
                    who = x["assigned_to"]           # the member it was assigned to
                else:
                    if who:
                        _count(outside, who)
                    continue
            p = people[who]
            p["resolved"] += 1
            g = grade(x.get("close_notes"), o)
            p["score"] += weight if g == "bare" else 1.0
            if x.get("table") == "change_task":
                p["ctask"] += 1
            else:
                p["hours"].append(_hours(x))
            p["notes"].append(x.get("close_notes"))
            if x.get("table") == "incident":
                p["inc"] += 1
                p["reopen"] += 1 if (x.get("reopen_count") or 0) > 0 else 0
                p["came_back"] += 1 if x.get("_came_back") else 0
        for x in items:
            if x.get("opened_by") in people and _in(_t(x.get("opened_at")), since, until):
                people[x["opened_by"]]["opened"] += 1
        rows = []
        for p in people.values():
            q, _ = _quality(p["notes"], o)
            rep = p["reopen"] if reopen_used else p["came_back"]
            rows.append({"name": p["name"], "resolved": p["resolved"], "score": round(p["score"], 1), "median_h": _r1(_median(p["hours"])),
                         "quality": q, "repeat": _r1(_pct(rep, p["inc"])) if p["inc"] else None, "opened": p["opened"], "ctask": p["ctask"]})
        rows.sort(key=lambda p: (-p["score"], -p["resolved"], p["name"]))
        elig = [p for p in rows if p["resolved"] >= int(_o(o, "star_min", 25))]
        awards = []
        if rows and max(p["resolved"] for p in rows) > 0:
            m = max(rows, key=lambda p: (p["resolved"], p["name"]))
            awards.append({"icon": "trophy", "what": "Most closed" if ct_read else "Most resolved", "name": m["name"],
                           "stat": "%d tickets %s" % (m["resolved"], "closed" if ct_read else "resolved")})
        if elig:
            b = max(elig, key=lambda p: (p["quality"][0], p["resolved"]))
            awards.append({"icon": "pen", "what": "Best close notes", "name": b["name"], "stat": "%d%% good notes" % b["quality"][0]})
            f = min([p for p in elig if p["median_h"] is not None] or elig, key=lambda p: (p["median_h"] if p["median_h"] is not None else 1e9))
            if f.get("median_h") is not None:
                awards.append({"icon": "bolt", "what": "Fastest to resolve", "name": f["name"], "stat": "median %s" % fmt_hours(f["median_h"])})
            c = min([p for p in elig if p["repeat"] is not None] or [None], key=lambda p: p["repeat"] if p else 0)
            if c:
                awards.append({"icon": "check", "what": "Fixed for good", "name": c["name"],
                               "stat": "only %.1f%% %s" % (c["repeat"], "reopened" if reopen_used else "came back")})
        tr = sum(p["resolved"] for p in rows)
        team_q = [round(sum(p["quality"][k] * p["resolved"] for p in rows) / tr) if tr else 0 for k in range(3)]
        reps = [p for p in rows if p["repeat"] is not None]
        op_rows, op_of = senders(team, roster)
        return {"people": rows[:25], "awards": awards,
                "total": {"resolved": tr + sum(outside.values()), "quality": team_q, "ctask": sum(p["ctask"] for p in rows),
                          "repeat": _r1(sum(p["repeat"] for p in reps) / len(reps)) if reps else None},
                "members_known": known, "members": len(roster), "outside": {"count": sum(outside.values()), "names": _top(outside, 3)},
                "openers": op_rows, "openers_of": op_of, "more": max(0, len(rows) - 25), "ctasks": ct_read}

    # sites, directorates and teams (0.97.0): every group with tickets has its place; a site or directorate with tickets
    # has a page, a team when it is one of the teams (team_min, teams_max, or named)
    seen_groups = set(it.get("group") for it in items if it.get("group"))
    gtree = r.get("group_tree") or {}
    tree_ok = not r.get("group_tree_error") and o.get("use_group_parents", True) not in (False, "false", "False", 0)
    hier = fixed["hier"] if fixed else servicenow_insights_hierarchy(sorted(seen_groups | set(teams)), o, gtree if tree_ok else {},
                                                                      tree_ok or str(o.get("hierarchy_source") or "parents") == "names")
    # no team page (their tickets still count at every level): excluded by name, inactive in ServiceNow, a group type
    # that is not a team (approvers, permissions ...) - 0.97.0
    gmeta = dict((v.get("name"), v) for v in gtree.values())
    pats = [str(x).strip().lower() for x in (o.get("exclude_groups") or []) if str(x).strip()]
    skip_types = [str(x).strip().lower() for x in (o.get("skip_group_types") or []) if str(x).strip()]
    folded = {}
    for g in sorted(seen_groups | set(teams)):
        meta = gmeta.get(g) or {}
        if any(fnmatch.fnmatchcase(g.lower(), x) for x in pats):
            folded[g] = "excluded (servicenow_insights_exclude_groups)"
        elif meta.get("active") is False and not o.get("inactive_teams"):
            folded[g] = "inactive in ServiceNow"
        elif any(x in str(meta.get("type") or "").lower() for x in skip_types):
            folded[g] = "group type %s" % meta.get("type")
    if fixed:
        folded = dict(fixed["folded"])
    teams = [t for t in teams if t not in folded]
    gcount = {}
    for it in items:
        if it.get("group"):
            gcount[it["group"]] = gcount.get(it["group"], 0) + 1
    site_g, dir_g = {}, {}
    for g, h in hier.items():
        site_g.setdefault(h["site"], set()).add(g)
        dir_g.setdefault((h["site"], h["dir"]), set()).add(g)
    sites = [] if org_is_map(o) else sorted(site_g, key=_level_key)       # the org map: its own tree, below
    scopes = {"all": scope_model(None)}
    scopes["all"].update({"site": "", "dir": "", "label": "All assignment groups", "path": [], "crumbs": [["all", "All"]], "parent": ""})
    tree, pages, used = [], [], set()

    def unique(slug, where):
        k, n = slug or "x", 2
        while (where, k) in used:
            k, n = "%s-%d" % (slug, n), n + 1
        used.add((where, k))
        return k
    for site in sites:
        skey, sslug = "site:" + site, unique(_slug(site), "")
        scopes[skey] = scope_model(None, site_g[site], "site", skey, site)
        scopes[skey].update({"site": site, "dir": "", "label": site, "path": [sslug], "parent": "all",
                             "crumbs": [["all", "All"], [skey, site]]})
        pages.append({"key": skey, "level": "site", "path": [sslug]})
        snode = {"key": skey, "label": site, "dirs": []}
        for d in sorted([d for (s_, d) in dir_g if s_ == site], key=_level_key):
            dkey, dslug = "dir:%s/%s" % (site, d), unique(_slug(d), sslug)
            scopes[dkey] = scope_model(None, dir_g[(site, d)], "dir", dkey, d)
            scopes[dkey].update({"site": site, "dir": d, "label": d, "path": [sslug, dslug], "parent": skey,
                                 "crumbs": [["all", "All"], [skey, site], [dkey, d]]})
            pages.append({"key": dkey, "level": "dir", "path": [sslug, dslug]})
            dnode = {"key": dkey, "label": d, "teams": []}
            for t in sorted([t for t in teams if hier.get(t, {}).get("site") == site and hier.get(t, {}).get("dir") == d],
                            key=lambda t: _nat(hier[t]["team"])):
                tslug = unique(_slug(hier[t]["team"]), sslug + "/" + dslug)
                tlab = hier[t]["team"] + ((" (under %s)" % hier[t]["under"]) if hier[t].get("under") else "")
                dnode["teams"].append({"key": t, "label": tlab})
                pages.append({"key": t, "level": "team", "path": [sslug, dslug, tslug]})
                scopes[t] = scope_model(t, None, "team", t, t)
                scopes[t].update({"site": site, "dir": d, "label": tlab, "path": [sslug, dslug, tslug], "parent": dkey,
                                  "crumbs": [["all", "All"], [skey, site], [dkey, d], [t, tlab]]})
            snode["dirs"].append(dnode)
        tree.append(snode)

    def side_row(k):
        s = scopes[k]
        return {"key": k, "team": s["name"], "label": s["label"], "slug": s["slug"], "backlog": s["backlog"],
                "series": [w["backlog"] for w in s["weeks"]], "old": s["age"][3], "median_h": s["median_h"][0], "sla": s["sla"][0], "resp": s["resp"][0],
                "hops_open": s["hops"]["open"], "hops_pct": s["hops"]["done_pct"], "hold_late": s["hold"]["late_n"], "review_n": s["codes"]["review_n"],
                "stale": s["closure"]["count"], "survey": s["survey"]["avg"], "fcr": s["fcr"]["now"], "unable": s["outcomes"]["unable"][0],
                "at_risk": s["at_risk"]["count"],
                "quality": s["quality"], "top": [s["issues"][0]["name"], s["issues"][0]["count"]] if s["issues"] else ["", 0],
                "incidents": s["counts"]["incidents"],
                "ct_closed": (s.get("ctasks") or {}).get("closed"), "ct_open": (s.get("ctasks") or {}).get("open"),
                "vip": (s.get("vip") or {}).get("unit")}
    # side by side: the All page its sites (or, with one site, its directorates; with one of those, its teams), a site its
    # directorates, a directorate its teams
    for snode in tree:
        dk = [d["key"] for d in snode["dirs"]]
        scopes[snode["key"]]["side"] = {"kind": "Directorates", "rows": [side_row(k) for k in dk]}
        for dnode in snode["dirs"]:
            scopes[dnode["key"]]["side"] = {"kind": "Teams", "rows": [side_row(t["key"]) for t in dnode["teams"]]}
    if len(tree) > 1:
        scopes["all"]["side"] = {"kind": "Sites", "rows": [side_row(n["key"]) for n in tree]}
    elif tree and len(tree[0]["dirs"]) > 1:
        scopes["all"]["side"] = {"kind": "Directorates", "rows": [side_row(d["key"]) for d in tree[0]["dirs"]]}
    else:
        scopes["all"]["side"] = {"kind": "Teams", "rows": [side_row(t["key"]) for n in tree for d in n["dirs"] for t in d["teams"]]}
    if not org_is_map(o):                                                             # the org map: once its teams are made, below
        scopes["all"]["teams"] = [side_row(t) for t in sorted(teams, key=_nat)]      # every team (the email's table, the workbook)
    ids = {"all": "all"}
    for i, n_ in enumerate(tree):
        ids[n_["key"]] = "S%d" % (i + 1)
    for i, k in enumerate([d["key"] for n_ in tree for d in n_["dirs"]]):
        ids[k] = "D%d" % (i + 1)
    for i, t in enumerate(teams):
        ids[t] = "T%d" % (i + 1)
    org, custom = [], {}
    una = fixed.get("unassigned", False) if fixed else any(not it.get("group") for it in items)   # a split's half: the whole's tree
    # the tickets' compact rows (1.8.0's Custom view; 1.34.0: every page's periods take its own groups' rows from them):
    # from the window before's start while it is read, and from the window's start for a page whose rows do not fit
    mx = int(_o(o, "custom_view_max_rows", 30000))
    gl = sorted([g for g in seen_groups], key=_nat) + ([""] if any(not it.get("group") for it in items) else [])
    page_periods = o.get("page_periods", True) not in (False, "false", "False", 0)
    rows_full = custom_rows(items, since, until, gl, o, prev if prev < since else since, tz, bh_spec) if (
        not fixed and (page_periods or (org_is_map(o) and o.get("custom_view", True) not in (False, "false", "False", 0)))) else None
    _win = []

    def rows_win():
        if not _win:
            _win.append(custom_rows(items, since, until, gl, o, since, tz, bh_spec))
        return _win[0]

    def why_before(cut):
        return ("the window before is over servicenow_insights_custom_view_max_rows (%s)" % "{:,}".format(mx)) if cut else (
            "servicenow_insights_compare is false" if not win.get("compare") else "the window before was not read whole this run")
    if org_is_map(o):
        # the org map (1.8.0): a page for every node at every depth (its groups' tickets rolled up), every group in the map
        # a team page under its node; the unmapped bucket and Unassigned last
        tset = set(teams)
        org = org_tree(hier, sorted(hier, key=_nat), una, o)          # every group placed (a split's half: the whole's)
        nid = {1: 0, 2: 0}
        for nd in org_walk(org):
            k = nd["key"]
            par = scopes["org:" + ORG_SEP.join(nd["path"][:-1])] if nd["depth"] > 1 else scopes["all"]
            pkey = "all" if nd["depth"] == 1 else "org:" + ORG_SEP.join(nd["path"][:-1])
            slug = unique(_slug(nd["label"]), "/".join(par["path"]))
            lv = "site" if nd["depth"] == 1 else "dir"
            scopes[k] = scope_model(None, nd["all"], lv, k, nd["label"])
            scopes[k].update({"site": nd["path"][0], "dir": " > ".join(nd["path"][1:]), "label": nd["label"], "path": par["path"] + [slug],
                              "parent": pkey, "crumbs": par["crumbs"] + [[k, nd["label"]]], "depth": nd["depth"],
                              "teams_under": [g for g in nd["all"] if g in tset]})
            pages.append({"key": k, "level": lv, "path": scopes[k]["path"]})
            nid[min(nd["depth"], 2)] += 1
            ids[k] = ("S%d" if nd["depth"] == 1 else "N%d") % nid[min(nd["depth"], 2)]
            for t in nd["groups"]:
                if t not in tset:
                    continue
                tp = scopes[k]["path"] + [unique(_slug(t), "/".join(scopes[k]["path"]))]
                scopes[t] = scope_model(t, None, "team", t, t)
                scopes[t].update({"site": nd["path"][0], "dir": " > ".join(nd["path"][1:]), "label": t, "path": tp, "parent": k,
                                  "crumbs": scopes[k]["crumbs"] + [[t, t]], "depth": nd["depth"] + 1})
                pages.append({"key": t, "level": "team", "path": tp})
        for nd in org_walk(org):
            kids = [c["key"] for c in nd["children"]]
            mine = [t for t in nd["groups"] if t in tset]
            scopes[nd["key"]]["side"] = {"kind": "Teams" if not kids else ("Units" if not mine else "Units and teams"),
                                         "rows": [side_row(x) for x in kids + mine]}
        scopes["all"]["side"] = {"kind": "Sites", "rows": [side_row(nd["key"]) for nd in org]}
        scopes["all"]["teams_under"] = list(teams)
        scopes["all"]["teams"] = [side_row(t) for t in sorted(teams, key=_nat)]
        if o.get("custom_view", True) not in (False, "false", "False", 0) and not fixed:
            # the periods' compare needs the window before's tickets too: carried while they fit, else the window only
            data = rows_full if rows_full["n"] <= mx or not prev < since else rows_win()
            if data is not rows_full:
                data["no_compare"] = True
            custom = data if data["n"] <= mx else {"skipped": data["n"], "max": mx}
            if custom.get("cols"):          # 1.16.0: one muted line under the period buttons - what was left out, and why
                custom["left_line"] = periods_left_line(custom.get("left"), why_before(custom.get("no_compare")))
    model = {"window": win, "url": r.get("url") or "", "prefixes": prefix_map(r.get("items"), o), "teams": teams, "scopes": scopes, "reopen_used": reopen_used,
             "tree": tree, "pages": pages, "scope_ids": ids, "hier": hier, "org": org, "custom": custom,
             "vip": dict((k_, v_) for k_, v_ in vip.items() if k_ != "of"),
             "org_bad": org_map(o.get("hierarchy_map"))[1] if org_is_map(o) else [],
             "org_bucket": (" ".join(str(o.get("unmapped_bucket") or UNKNOWN).split()) or UNKNOWN) if org_is_map(o) else "",
             "hier_other": sorted([g for g, h in hier.items() if h["dir"] == OTHER], key=_nat),
             "hier_unknown": [[g, gcount.get(g, 0)] for g in sorted([g for g, h in hier.items() if h["rule"] == "unknown"], key=_nat)],
             "hier_folded": [[g, why, "%s / %s" % (hier[g]["site"], hier[g]["dir"])] for g, why in sorted(folded.items(), key=lambda kv: _nat(kv[0]))
                             if g in hier],
             "placed_by": dict((b, sum(1 for h in hier.values() if h["by"] == b)) for b in sorted(set(h["by"] for h in hier.values()))),
             "group_tree_error": str(r.get("group_tree_error") or ""),
             "reopened_n": reopened, "incidents_n": len(inc_win), "short": short_names(group_names),
             "members_read": members_known, "members_error": str(r.get("members_error") or ""),
             "tables": r.get("tables") or [], "groups": r.get("groups") or [], "audit_error": r.get("audit_error") or "",
             "cut": cut_lines(r), "compare_cut": compare_cut_line(r.get("compare_cut")), "surveys_read": survey_read_lines(survey_plan, o),
             "audit_read": bool(audit) or not r.get("audit_error"), "lookup_note": r.get("lookup_note") or "",
             "audit_source": r.get("audit_source") or ("sys_audit" if audit else ""), "audit_note": r.get("audit_note") or "",
             "act": {"on": bool(o.get("act")), "ran": act_ran, "asked": int(state.get("asked") or 0), "answered": int(state.get("answered") or 0),
                     "errors": list(state.get("errors") or [])[:20], "model": state.get("model") or "", "provider": state.get("provider") or "",
                     "issues": len(state.get("issues") or []), "fixes": len(state.get("fixes") or {}), "reason": str(o.get("act_reason") or ""),
                     "work_notes": dict(state.get("work_notes") or {}), "cached": int(state.get("cached") or 0)},
             "mask": dict(mask.get("counts") or {}), "summaries_by": "rules",
             "slo": {"source": sla_src, "asked": sla_r.get("asked") or "", "note": sla_r.get("note") or "", "response_measured": resp_measured,
                     "open_cut": list(sla_r.get("open_cut") or []),
                     "definitions": sorted([[nm, k_, c_] for (nm, k_), c_ in _sla_defs(items, since, until).items()], key=lambda e: (e[1], -e[2], e[0])),
                     "business_hours": business_hours_label(o.get("business_hours"), win.get("time_zone")),
                     "first_assigned_error": str(r.get("first_assigned_error") or ""), "categories": cat_list},
             "process": {"states_error": str(r.get("states_error") or ""), "state_metric": str(o.get("state_metric") or ""),
                         "paused_error": str((r.get("sla") or {}).get("paused_error") or ""), "survey_error": str(r.get("survey_error") or ""),
                         "survey_read": "survey_error" in r and not r.get("survey_error"), "hop_limit": hop_limit}}
    for k, s in scopes.items():
        s["summary"] = rule_summary(s, model, o)
    # 1.59.0: about to breach - each page's own groups (the All page: every group read)
    if (r.get("breach") or {}).get("asked"):
        model["breach_read"] = {"error": str(r["breach"].get("error") or ""), "breached_error": str(r["breach"].get("breached_error") or "")}
        for k, s in scopes.items():
            s["breach"] = breach_model(r["breach"], dict(o, time_zone=win.get("time_zone")),
                                       None if s.get("groups_of") is None else set(s["groups_of"]))
    # 1.15.0: the team lead - the group's manager in ServiceNow (sys_user_group.manager), on the team's page and email only
    model["leads"] = dict((g, dict(m)) for g, m in (r.get("managers") or {}).items() if isinstance(m, dict) and m.get("name"))
    model["leads_error"] = str(r.get("managers_error") or "")
    if o.get("show_lead", True) not in (False, "false", "False", 0):
        for k, s in scopes.items():
            if s.get("level") == "team" and model["leads"].get(s.get("team")):
                s["lead"] = lead_label(model["leads"][s["team"]])
    # the split (0.97.0): every page's figures again for the groups that match servicenow_insights_split_pattern (the group
    # or a parent team below the root) and for the rest - worked out here, so the page only switches between them
    label, pat = str(o.get("split_label") or "").strip(), str(o.get("split_pattern") or "").strip().lower()
    by, mapm = str(o.get("split_by") or "both").strip().lower(), org_is_map(o)
    if label and (pat or (mapm and by in ("level", "both"))) and not fixed:
        def is_split(g):
            if mapm:                   # the org map (1.8.0): the pattern, or the label as one of the group's levels (split_by)
                return ((by in ("pattern", "both") and bool(pat) and fnmatch.fnmatchcase(g.lower(), pat))
                        or (by in ("level", "both") and label.lower() in [x.lower() for x in (hier.get(g) or {}).get("path") or []]))
            below = (group_chain(g, gtree) if tree_ok else [])[:-1]
            return fnmatch.fnmatchcase(g.lower(), pat) or any(fnmatch.fnmatchcase(a.lower(), pat) for a in below)
        sgroups = sorted([g for g in hier if is_split(g)], key=_nat)
        model["split"] = {"label": label, "groups": sgroups}
        model["variants"] = {}
        for v in ("in", "out"):
            rv = dict(r, items=[it for it in r.get("items") or [] if (it.get("group") in sgroups) == (v == "in")])
            if r.get("breach"):
                rv["breach"] = _breach_r(r, set(sgroups) if v == "in" else set(
                    x.get("group") for x in (r["breach"].get("open") or []) + (r["breach"].get("breached") or []) if x.get("group") not in sgroups))
            mv = servicenow_insights_model(rv, state, mask, dict(o, split_pattern="", split_label="",
                                                                 _fixed={"teams": teams, "hier": hier, "folded": folded, "unassigned": una}))
            model["variants"][v] = mv["scopes"]
    if custom.get("cols"):                        # the Custom view's presets: the split's two halves, and the user's own
        custom["presets"] = custom_presets(hier, custom["groups"], model.get("split"), o)
    if page_periods and rows_full is not None:
        # 1.34.0: what each page's period bar takes its rows from - the window before as well while the page's rows fit
        # servicenow_insights_custom_view_max_rows, else the window only; "custom" = the same rows as the Custom view's
        model["rows"] = {"max": mx, "full": "custom" if custom.get("cols") and not custom.get("no_compare") else rows_full,
                         "win": ("custom" if custom.get("no_compare") else rows_win()) if rows_full["n"] > mx and prev < since else None,
                         "why_before": why_before(True), "why_none": why_before(False)}
    return model


def lead_label(m):
    """A team lead as the page and the email show it: the name, and (inactive) when ServiceNow has the account inactive."""
    return m["name"] + ("" if m.get("active", True) else " (inactive in ServiceNow)")


def _lead_patterns(want):
    """servicenow_insights_email_leads: a list of group names or patterns (fnmatch, any case), or "all"."""
    if isinstance(want, str):
        want = [] if not want.strip() else (["*"] if want.strip().lower() == "all" else [x for x in re.split(r"[,\n]", want)])
    pats = [" ".join(str(x).split()).lower() for x in want or [] if str(x or "").strip()]
    return ["*" if x == "all" else x for x in pats]


def servicenow_insights_lead_mail(model, email_leads=None, team_email_to=None):
    """Who gets each team's own email (1.15.0) -> {to: {team: [addresses]}, lines: [the job output's lines]}. A team that
    servicenow_insights_email_leads names (a name, a pattern, or "all") goes to its manager in ServiceNow - not when the group
    has no manager, the manager is inactive or has no email (a line each); servicenow_insights_team_email_to wins for a team
    it names. Only the teams with a page."""
    m = model or {}
    teams = [p["key"] for p in m.get("pages") or [] if p.get("level") == "team"]
    pats, leads, to, lines = _lead_patterns(email_leads), m.get("leads") or {}, {}, []
    fixed = dict((k, v) for k, v in (team_email_to or {}).items() if v)
    picked = [t for t in teams if any(fnmatch.fnmatchcase(t.lower(), x) for x in pats)] if pats else []
    if picked and m.get("leads_error"):
        lines.append("Team leads not emailed: the managers were not read (%s)" % m["leads_error"])
        picked = []
    for t in picked:
        if t in fixed:
            continue
        ld = leads.get(t)
        why = ("no manager in ServiceNow" if not ld else "the manager %s is inactive" % ld["name"] if not ld.get("active", True)
               else "the manager %s has no email address" % ld["name"] if not ld.get("email") else "")
        if why:
            lines.append("%s: not emailed - %s" % (t, why))
        else:
            to[t] = [ld["email"]]
    for t, v in fixed.items():
        to[t] = [v] if isinstance(v, str) else list(v)
    if pats and not picked and not m.get("leads_error"):
        lines.append("Team leads: servicenow_insights_email_leads matches no team page (%s)" % ", ".join(pats))
    return {"to": to, "lines": lines}


def masked_excerpt(text, mask, n=160):
    """A close note's start for leadership's list: the names, phone numbers, addresses and hosts the mask knows replaced by
    [person], [phone] ... - never the mask's own placeholders."""
    t = PLACEHOLDER.sub(lambda m: "[%s]" % {"ID": "id"}.get(m.group(1), m.group(1).lower()), apply_mask(text, mask))
    t = EMAIL.sub("[email]", PHONE.sub("[phone]", t))
    t = " ".join(t.split())
    return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"


def survey_grade(avg, scale, n, opts=None):
    """1.16.0: Good / Fair / Low by the share of the scale, (avg - min) / (max - min): at or above grades.good (0.8) Good, at
    or above grades.fair (0.6) Fair, else Low. '' with fewer than survey_min_answers (5) answers or no scale known."""
    o = opts or {}
    if avg is None or not scale or scale[1] is None or scale[0] is None or scale[1] <= scale[0]:
        return ""
    if int(n or 0) < int(_o(o, "survey_min_answers", 5)):
        return ""
    g = o.get("survey_grades") or {}
    good, fair = float(g.get("good", 0.8)), float(g.get("fair", 0.6))
    share = (float(avg) - scale[0]) / float(scale[1] - scale[0])
    share = round(share, 9)                                      # 0.8 exactly is Good, not lost to a float's last digit
    return "Good" if share >= good else ("Fair" if share >= fair else "Low")


def _scale_label(sc):
    if not sc:
        return ""
    f = lambda x: ("%d" % x) if float(x) == int(x) else ("%g" % x)    # noqa: E731
    return "%s to %s%s" % (f(sc[0]), f(sc[1]), ", as observed" if sc[2] == "observed" else "")


def survey_questions(rows, meta, opts, since, until):
    """1.16.0: every question read -> {questions: {key: {...}}, surveys: [...], headline: {...}}. A question is a rating
    when ServiceNow says so (datatype scale, numericscale or template, its max 10 or less), or - its datatype not read -
    when every answer is a whole number from 0 to 10 with 3 or more values; a question named in
    servicenow_insights_survey_metrics always. Its scale: ServiceNow's min and max (a template's values), else the answers'
    (as observed). A survey retired in ServiceNow keeps its own block, never blended with the active one."""
    o = opts or {}
    named = set(str(x).strip().lower() for x in name_list(o.get("survey_metrics") or []) if str(x).strip())
    qs = {}
    for k_, key, v, t_ in rows:
        q = qs.setdefault(key, {"vals": [], "n": 0})
        q["vals"].append(v)
        if _in(_t(t_), since, until):
            q["n"] += 1
    out = {}
    for key, q in qs.items():
        m = meta.get(key) or {}
        name = m.get("name") or str(key)
        dt = str(m.get("datatype") or "").lower()
        lo, hi = min(q["vals"]), max(q["vals"])
        rmin, rmax = m.get("min"), m.get("max")
        read = rmin is not None and rmax is not None and rmax > rmin
        observed = [lo, hi, "observed"] if hi > lo else None
        scale = [rmin, rmax, "read"] if read else observed
        if name.lower() in named:
            rating, why = True, "named"
        elif dt:
            top = rmax if read else hi
            rating = dt in ("scale", "numericscale", "template") and top is not None and top <= 10
            why = "datatype %s%s" % (dt, "" if rating else (", up to %g" % top if top is not None and top > 10 else ""))
        else:
            whole = all(float(x) == int(x) for x in q["vals"])
            rating = whole and lo >= 0 and hi <= 10 and len(set(q["vals"])) >= 3
            why = "the answers"
        out[key] = {"key": key, "name": name, "survey": m.get("survey") or "", "state": m.get("state") or "", "datatype": dt,
                    "n": q["n"], "lo": lo, "hi": hi, "scale": scale, "rating": rating, "why": why}
    surveys = {}
    for q in sorted(out.values(), key=lambda q: (_nat(q["survey"]), _nat(q["name"]))):
        sv = surveys.setdefault((q["survey"], q["state"]), {"survey": q["survey"], "state": q["state"], "keys": [], "other": []})
        (sv["keys"] if q["rating"] else sv["other"]).append(q["key"])
    blocks = []
    for sv in surveys.values():
        if not sv["keys"]:
            continue
        scales = set(tuple(out[k]["scale"][:2]) for k in sv["keys"] if out[k]["scale"])
        sv["one_scale"] = len(scales) == 1 and all(out[k]["scale"] for k in sv["keys"])
        sv["scale"] = out[sv["keys"][0]]["scale"] if sv["one_scale"] else None
        main = max(sv["keys"], key=lambda k: (out[k]["n"], -sv["keys"].index(k)))
        sv["main"] = sv["keys"] if sv["one_scale"] else [main]
        sv["main_label"] = (sv["survey"] or "Survey") if sv["one_scale"] and len(sv["keys"]) > 1 else out[main]["name"]
        sv["n"] = sum(out[k]["n"] for k in sv["keys"])
        blocks.append(sv)
    blocks.sort(key=lambda b: (b["state"] == "retired", -b["n"], _nat(b["survey"])))
    head = None
    typed = " ".join(str(o.get("survey_headline") or "").split())        # said back as typed (1.17.0); matched in any case
    want = typed.lower()
    if want:
        for b in blocks:
            if b["survey"].lower() == want:
                head = {"keys": b["main"], "label": b["main_label"] if b["one_scale"] else "%s · %s" % (b["survey"] or "Survey", b["main_label"]),
                        "scale": b["scale"] if b["one_scale"] else out[b["main"][0]]["scale"], "survey": b["survey"], "state": b["state"]}
                break
        if head is None:
            cand = sorted([q for q in out.values() if q["rating"] and q["name"].lower() == want],
                          key=lambda q: (q["state"] == "retired", -q["n"]))
            if cand:
                q = cand[0]
                head = {"keys": [q["key"]], "label": ("%s · %s" % (q["survey"], q["name"])) if q["survey"] else q["name"], "scale": q["scale"],
                        "survey": q["survey"], "state": q["state"]}
    if head is None and blocks:
        b = blocks[0]                       # the active survey with the most answers (a retired one only when none is active)
        head = {"keys": b["main"], "label": b["main_label"] if b["one_scale"] and len(b["keys"]) > 1 else
                ("%s · %s" % (b["survey"], b["main_label"]) if b["survey"] else b["main_label"]),
                "scale": b["scale"] if b["one_scale"] else out[b["main"][0]]["scale"], "survey": b["survey"], "state": b["state"]}
    other = [out[k] for sv in surveys.values() for k in sv["other"]]
    return {"questions": out, "blocks": blocks, "headline": head, "other": other,
            "headline_missing": typed if want and head is not None and want not in (head["label"].lower(), (head["survey"] or "").lower())
            and not any(out[k]["name"].lower() == want for k in head["keys"]) else ""}


def survey_read_lines(plan, opts=None):
    """1.16.0: How this was made - every question read: survey (its state) - question - answers in the window - scale; the
    ones not shown as a rating said so, with how to show one."""
    out = []
    qs = plan.get("questions") or {}
    for q in sorted(qs.values(), key=lambda q: (q["state"] == "retired", _nat(q["survey"]), not q["rating"], _nat(q["name"]))):
        who = "%s%s" % (q["survey"] or "(survey not read)", " (retired)" if q["state"] == "retired" else "")
        if q["rating"]:
            out.append("%s - %s - %d answers - %s" % (who, q["name"], q["n"], _scale_label(q["scale"]) if q["scale"] else "scale not known: no grade"))
        else:
            out.append("%s - %s - %d answers - read, not shown as a rating (%s; values %g to %g): name it in "
                       "servicenow_insights_survey_metrics to show it" % (who, q["name"], q["n"], q["why"], q["lo"], q["hi"]))
    h = plan.get("headline")
    if h:
        out.append("The headline (the tile, the email, the history): %s, %s%s" % (h["label"], _scale_label(h["scale"]) if h["scale"] else "scale not known",
                                                                              " - servicenow_insights_survey_headline" if (opts or {}).get("survey_headline") and not plan.get("headline_missing") else ""))
    if plan.get("headline_missing"):
        out.append("servicenow_insights_survey_headline: %s is not a survey or rating question read - the default instead" % plan["headline_missing"])
    return out


def survey_scope(plan, rows, mine_ids, grp_of, since, until, prev, tz, opts):
    """1.16.0: a scope's satisfaction - the headline's figures (tile, email, history), each survey's block (its questions,
    by month and by group, each graded on its own scale), the questions read that are not ratings."""
    o = opts or {}
    qs = plan["questions"]
    mine = [x for x in rows if x[0] in mine_ids]

    def fig(keys, scale):
        ks = set(keys)
        now = [v for k_, q, v, t_ in mine if q in ks and _in(_t(t_), since, until)]
        before = [v for k_, q, v, t_ in mine if q in ks and _in(_t(t_), prev, since)]
        mon, grp = {}, {}
        for k_, q, v, t_ in mine:
            if q in ks and _in(_t(t_), since, until):
                mon.setdefault(_t(t_).astimezone(tz).strftime("%Y-%m"), []).append(v)
                grp.setdefault(grp_of.get(k_), []).append(v)
        avg = _r2(_avg(now))
        return {"n": len(now), "avg": avg, "prev": _r2(_avg(before)), "prev_n": len(before), "grade": survey_grade(avg, scale, len(now), o),
                "months": [[k_, _r2(_avg(v)), len(v), survey_grade(_avg(v), scale, len(v), o)] for k_, v in sorted(mon.items())],
                "by_group": sorted([{"group": g, "avg": _r2(_avg(v)), "n": len(v), "grade": survey_grade(_avg(v), scale, len(v), o)}
                                    for g, v in grp.items()], key=lambda e: (-e["n"], e["group"]))[:12]}
    h = plan.get("headline")
    out = dict(fig(h["keys"], h["scale"]) if h else fig([], None), label=h["label"] if h else "", scale=h["scale"] if h else None,
               survey=h["survey"] if h else "", state=h["state"] if h else "", blocks=[], not_rating=[],
               questions_n=sum(len(b["keys"]) for b in plan["blocks"]),
               metrics=sorted(set(qs[q]["name"] for k_, q, v, t_ in mine if q in qs)))
    for b in plan["blocks"]:
        msc = b["scale"] if b["one_scale"] else qs[b["main"][0]]["scale"]
        main = fig(b["main"], msc)
        if not main["n"] and not any(x[1] in b["keys"] for x in mine):
            continue
        out["blocks"].append({"survey": b["survey"], "state": b["state"], "scale": b["scale"], "one_scale": b["one_scale"],
                              "main_label": b["main_label"], "main": main, "main_scale": msc, "overall": len(b["keys"]) > 1 and b["one_scale"],
                              "questions": [dict(fig([k], qs[k]["scale"]), name=qs[k]["name"], scale=qs[k]["scale"]) for k in b["keys"]]})
    for q in plan["other"]:
        n_ = sum(1 for k_, k, v, t_ in mine if k == q["key"] and _in(_t(t_), since, until))
        if n_:
            out["not_rating"].append({"survey": q["survey"], "name": q["name"], "n": n_, "lo": q["lo"], "hi": q["hi"], "why": q["why"]})
    return out


def _avg(vals):
    v = [float(x) for x in vals if x is not None]
    return sum(v) / len(v) if v else None


def _r2(x):
    return None if x is None else round(x, 2)


def business_days(a, b, spec, tz):
    """Whole business days from a to b: the days after a's up to b's that are business days (spec's days, not its holidays;
    Mon-Fri without business hours)."""
    if a is None or b is None or b <= a:
        return 0 if a is not None and b is not None else None
    days, hol = (spec[0], spec[3]) if spec else (set(range(5)), set())
    d, last, n = a.astimezone(tz).date() + datetime.timedelta(days=1), b.astimezone(tz).date(), 0
    while d <= last:
        if d.weekday() in days and d.isoformat() not in hol:
            n += 1
        d += datetime.timedelta(days=1)
    return n


def _sla_defs(items, since, until):
    """{(SLA definition name, kind): tickets done in the window that carried it}."""
    out = {}
    for it in items:
        if _in(_t(it.get("done_at")), since, until):
            for nm, k in set(tuple(x) for x in it.get("_slas") or []):
                _count(out, (nm, k))
    return out


def vip_tiers(r, o):
    """1.43.0: the VIP tiers read (the module's vip) -> {on, tiers, of, error, hidden, people, min}; on False when
    servicenow_insights_vip_fields is empty or the read is from before 1.43.0."""
    v = (r or {}).get("vip")
    if not isinstance(v, dict) or not v.get("tiers"):
        return {"on": False, "tiers": [], "of": {}, "error": "", "hidden": [], "people": [], "min": 5}
    return {"on": True, "tiers": [str(t) for t in v["tiers"]], "of": dict(v.get("of") or {}), "error": str(v.get("error") or ""),
            "hidden": [str(c) for c in v.get("hidden") or []], "people": list(v.get("people") or ["caller_id"]),
            "min": int(_o(o or {}, "vip_min", 5))}


def vip_of(it, vip):
    """The ticket's tier index (the first tier listed wins, across the people asked), or None (not VIP)."""
    ts = [vip["of"][it[k]] for k in vip["people"] if it.get(k) and it[k] in vip["of"]]
    return min(ts) if ts else None


def vip_scope(inc_now, ritm_now, flow, since, until, vip, o=None):
    """1.43.0: one scope's VIP tickets - a row per tier and Not VIP (incidents and requests opened in the window; of those
    done in it the median hours to respond (the response SLAs' elapsed) and resolve, response and resolution met, no SLA;
    open now), the incidents by priority and tier, each tier's SLA definitions (tickets, met, missed, met %, median
    elapsed), and the unit line (side by side). Under vip_min tickets a row has its counts only."""
    if not vip["on"]:
        return None
    n = len(vip["tiers"])
    keys = list(range(n)) + [None]
    done = [x for x in flow if _in(_t(x.get("done_at")), since, until)]
    open_now = [x for x in flow if _t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)]

    def resp_h(lst):
        return _r1(_median([e[3] for x in lst for e in x.get("_sla_recs") or [] if e[1] == "response" and e[3] is not None]))

    def row(sel, label, k):
        i_, r_ = [x for x in inc_now if sel(x)], [x for x in ritm_now if sel(x)]
        d_ = [x for x in done if sel(x)]
        rs, rl = [x for x in d_ if x.get("_resp") is not None], [x for x in d_ if x.get("_resol") is not None]
        small = len(i_) + len(r_) < vip["min"]
        return {"label": label, "tier": k, "incidents": len(i_), "requests": len(r_), "open": sum(1 for x in open_now if sel(x)),
                "done": len(d_), "small": small, "no_slo": sum(1 for x in d_ if x.get("_no_slo")),
                "resp_h": None if small else resp_h(d_), "median_h": None if small else _r1(_median([_hours(x) for x in d_])),
                "resp": None if small else _r1(_pct(sum(1 for x in rs if x["_resp"]), len(rs))),
                "resol": None if small else _r1(_pct(sum(1 for x in rl if x["_resol"]), len(rl)))}
    rows = [row(lambda x, k=k: x.get("_vip") == k, vip["tiers"][k] if k is not None else "Not VIP", k) for k in keys]
    prio = {}
    for x in inc_now:
        pk = x.get("priority_value") or "9"
        e = prio.setdefault(pk, {"label": priority_label(pk if pk != "9" else "", x.get("priority"), o), "value": pk, "n": [0] * (n + 1)})
        e["n"][x["_vip"] if x.get("_vip") is not None else n] += 1
    defs = []
    for k in range(n):
        by = {}
        for x in done:
            if x.get("_vip") == k:
                for nm, kind, br, el in x.get("_sla_recs") or []:
                    e = by.setdefault(nm, {"name": nm, "kind": kind, "tickets": set(), "met": 0, "missed": 0, "el": []})
                    e["tickets"].add(x.get("sys_id"))
                    e["missed" if br else "met"] += 1
                    if el is not None:
                        e["el"].append(el)
        lst = []
        for e in sorted(by.values(), key=lambda e: (e["kind"] != "response", -len(e["tickets"]), e["name"])):
            small = len(e["tickets"]) < vip["min"]
            lst.append({"name": e["name"], "kind": e["kind"], "tickets": len(e["tickets"]), "met": e["met"], "missed": e["missed"],
                        "pct": None if small else _r1(_pct(e["met"], e["met"] + e["missed"])), "median_h": None if small else _r1(_median(e["el"]))})
        defs.append({"tier": k, "label": vip["tiers"][k], "rows": lst})
    anyv = lambda x: x.get("_vip") is not None                    # noqa: E731
    unit = row(anyv, "VIP", -1)
    unit["n"] = [r_["incidents"] + r_["requests"] for r_ in rows[:n]]
    return {"rows": rows, "prio": sorted(prio.values(), key=lambda e: e["value"]), "defs": defs, "unit": unit,
            "total": sum(r_["incidents"] + r_["requests"] for r_ in rows[:n])}


def business_hours_label(bh, tz_name=""):
    """'Mon-Fri 07:30-16:30 (America/New_York), 2 holidays' - or 'calendar hours'."""
    spec = business_hours_spec(bh)
    if spec is None:
        return "calendar hours"
    days, sm, em, hol = spec
    names = [WEEKDAYS[d].capitalize() for d in sorted(days)]
    run = sorted(days)
    span = ("%s-%s" % (names[0], names[-1])) if len(run) > 2 and run == list(range(run[0], run[-1] + 1)) else ", ".join(names)
    return "%s %02d:%02d-%02d:%02d%s (%s)%s" % (span, sm // 60, sm % 60, em // 60 % 24, em % 60, " overnight" if em <= sm else "",
                                               tz_name or "UTC", (", %d holiday%s" % (len(hol), "" if len(hol) == 1 else "s")) if hol else "")


def fmt_hours(h):
    if h is None:
        return "—"
    if h >= 48:
        return "%.1f days" % (h / 24.0)
    return "%.1f h" % h


def _change(now, prev, pts=False):
    if now is None or prev is None:
        return ""
    d = (now - prev) if pts else ((now - prev) / prev * 100.0 if prev else 0)
    if abs(d) < 0.05:
        return "no change"
    return "%s%.1f%s" % ("▲ " if d > 0 else "▼ ", abs(d), " pts" if pts else "%")


def rule_summary(s, model, opts=None):
    """The summary when ACT did not write it: a few sentences from the numbers."""
    out = []
    iss = s["issues"][:3]
    tot = s["counts"]["incidents"]
    if iss and tot:
        share = round(100.0 * sum(i["count"] for i in iss) / tot)
        out.append("%s make%s up %d%% of the %s incidents: %s." % (
            "The top issue" if len(iss) == 1 else "The top %d issues" % len(iss), "s" if len(iss) == 1 else "", share,
            "{:,}".format(tot), ", ".join("%s (%d)" % (i["name"], i["count"]) for i in iss)))
    b = s["backlog"]
    if b[1] or b[0]:
        out.append("The backlog went from %d to %d (%s). %d %s older than %s days." % (
            b[1], b[0], _change(b[0], b[1]) or "no change", s["age"][3], "is" if s["age"][3] == 1 else "are", int(_o(opts, "old_days", 30))))
    if s["majors"]:
        chg = sum(1 for m in s["majors"] if m["change"])
        out.append("%d P1/P2 incident%s%s." % (len(s["majors"]), "" if len(s["majors"]) == 1 else "s",
                                               (", %d of them caused by a change" % chg) if chg else ""))
    if s["requests"]:
        out.append("The most requested service is %s (%d)." % (s["requests"][0]["name"], s["requests"][0]["count"]))
    if s.get("notes_graded"):
        out.append("%d%% of close notes say what was done. %d%% are a stock phrase such as \"fixed\" or \"done\", or empty." % (
            s["quality"][0], s["quality"][2]))
    return out


def servicenow_insights_summary_evidence(model, mask=None):
    lines = []
    for k, s in (model or {}).get("scopes", {}).items():
        sid = (model.get("scope_ids") or {}).get(k) or ("all" if k == "all" else "T%d" % ((model["teams"].index(k)) + 1))
        lines.append("SCOPE %s: %s%s" % (sid, {"site": "site ", "dir": "directorate "}.get(s.get("level"), ""), s["name"]))
        lines.append("incidents %d (window before %d); requests %d; backlog %d (at the start %d); open over 30 days %d; "
                     "median time to resolve %s (before %s); SLA met %s%% (before %s%%); bare close notes %d%% (before %d%%); "
                     "reassigned 3+ times %d; P1/P2 %d (%d caused by a change); changes %d, failed %d, backed out %d" % (
                         s["counts"]["incidents"], s["counts"]["incidents_prev"], s["counts"]["requests"], s["backlog"][0], s["backlog"][1],
                         s["age"][3], fmt_hours(s["median_h"][0]), fmt_hours(s["median_h"][1]), s["sla"][0], s["sla"][1],
                         s["quality"][2], s["quality_prev"][2], s["handoffs"]["count"], len(s["majors"]),
                         sum(1 for m in s["majors"] if m["change"]), s["changes"]["count"], s["changes"]["failed"], s["changes"]["backed_out"]))
        for i in s["issues"][:5]:
            lines.append("  issue: %s - %d (before %d), median fix %s, %d%% bare notes%s" % (
                apply_mask(i["name"], mask), i["count"], i["prev"], fmt_hours(i["median_h"]), i["quality"][2],
                ", a request opened as an incident" if i["kind"] == "request" else ""))
        for q in s["requests"][:3]:
            lines.append("  request: %s - %d (before %d)" % (q["name"], q["count"], q["prev"]))
        for p in s["problems"][:3]:
            lines.append("  problem candidate: %s on %s - %d in the last 30 days, %s" % (
                apply_mask(p["issue"], mask), apply_mask(p["ci"], mask), p["last"], ("problem " + p["problem"]) if p["problem"] else "no problem record"))
        lines.append("")
    return "\n".join(lines)


def servicenow_insights_summary_task(model, opts=None):
    o = opts or {}
    ids = list(((model or {}).get("scope_ids") or {}).values()) or ["all"] + ["T%d" % (i + 1) for i in range(len((model or {}).get("teams") or []))]
    return "\n".join([
        "Write what each scope's ticket figures say, for IT management: the evidence piped to you holds, per scope (the whole "
        "site, each site, directorate and team), the figures of a %s window of ServiceNow tickets against the window before, the top issues, "
        "requests and problem candidates. You cannot run any commands in this task, and none are needed." % (
            ("%d-month" % model["window"]["months"]) if (model or {}).get("window", {}).get("months") else "multi-week"),
        "",
        "For each scope give 3 or 4 bullets: what stands out and what to act on - the issues that make up most of the work, whether "
        "the backlog is growing, close notes, P1/P2s and changes, a self-service or catalog opportunity. One plain sentence of at most "
        "35 words each, with the numbers; use only the evidence; invent nothing; no cost or budget wording; no markdown.",
        "",
        "End your answer with exactly one JSON object between a line %s and a line %s, nothing else between them:" % (MARK_BEGIN, MARK_END),
        MARK_BEGIN,
        '{"items": [%s]}' % ", ".join('{"id": "%s", "lines": ["..."]}' % i for i in ids[:3]),
        MARK_END,
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def servicenow_insights_summary_merge(model, raw, mask=None, ran=True, reason="", answer_model=""):
    m = json.loads(json.dumps(model))
    note = _answer_note(raw, ran, reason)
    if note:
        m["act"]["errors"] = (m["act"].get("errors") or []) + ["the summaries: %s" % note]
        return m
    obj = _parse_json(raw) or {}
    ids = {"all": "all"}
    for k, v in (m.get("scope_ids") or {}).items():
        ids[v] = k
        ids[v.lower()] = k
    for i, t in enumerate(m.get("teams") or []):
        ids.setdefault("T%d" % (i + 1), t)
        ids.setdefault("t%d" % (i + 1), t)
    got = 0
    for x in obj.get("items") or []:
        if not isinstance(x, dict):
            continue
        k = ids.get(str(x.get("id") or "").strip())
        lines = [unmask(_clean(s, 400), mask) for s in (x.get("lines") or []) if _clean(s, 400)]
        if k and lines and k in m["scopes"]:
            m["scopes"][k]["summary"] = lines[:5]
            m["scopes"][k]["summary_by"] = "act"
            got += 1
    m["act"]["answered"] = int(m["act"].get("answered") or 0) + 1
    m["act"]["asked"] = int(m["act"].get("asked") or 0) + 1
    if answer_model and not m["act"].get("model"):
        m["act"]["model"] = answer_model
    if got:
        m["summaries_by"] = "act"
    return m


def servicenow_insights_finish(model, mask=None):
    """ACT's words with the masked values put back (issue names, fixes, knowledge drafts)."""
    return _unmask_deep(model, mask) if mask else model


# ---- the email's report, the workbook, the artifact ---------------------------------------------------------------

# ---- about to breach (1.59.0) -----------------------------------------------------------------------------------------

BREACH_BANDS = (1, 4)                 # the tiles' bands in business hours; the window (servicenow_breach_window_hours) is the last


def breach_bands(window_h):
    w = float(window_h or 8)
    return [float(b) for b in BREACH_BANDS if b < w] + [w]


def fmt_left(h):
    """Business time left in words: 24 min, 1 h 36 min, 7 h 06 min."""
    m = int(round(max(0.0, float(h or 0)) * 60))
    return ("%d min" % m) if m < 60 else ("%d h %02d min" % (m // 60, m % 60))


def fmt_band(b):
    return ("%d h" % b) if float(b) == int(b) else ("%g h" % b)


def servicenow_insights_breach_horizon(window_h, business_hours_setting=None):
    """The clock hours ahead the running SLAs are read (site_servicenow_insights breach_horizon_hours): the window itself in
    calendar hours; with business hours, the whole business days it takes and 4 days more (a weekend, a holiday). The report
    keeps only those within the window of business time."""
    w = float(window_h or 8)
    spec = business_hours_spec(business_hours_setting or {})
    if spec is None:
        return w
    per_day = (((spec[2] - spec[1]) % 1440) or 1440) / 60.0
    return float((int(-(-w // per_day)) + 4) * 24)


def _breach_at(pe, now, tz):
    """When an SLA breaches: 08:24 today, else Mon 16 Nov 08:24 (local)."""
    if pe is None:
        return ""
    lt = pe.astimezone(tz)
    return lt.strftime("%H:%M") if lt.date() == now.astimezone(tz).date() else lt.strftime("%a %d %b %H:%M")


def _span(a, b):
    """7-13 Nov, 31 Oct - 6 Nov."""
    if a.month == b.month:
        return "%d-%d %s" % (a.day, b.day, b.strftime("%b"))
    return "%d %s - %d %s" % (a.day, a.strftime("%b"), b.day, b.strftime("%b"))


def breach_model(b, opts=None, groups=None):
    """1.59.0, about to breach: the SLAs running now whose business time left is within servicenow_breach_window_hours -
    one row per ticket (its SLA with the least time left), least time left first - with the tiles (within 1 h, 4 h, the
    window; breached today), and the breached SLAs of the last servicenow_breach_days by day (a 7-day average each day),
    the last three weeks side by side. Time left: ServiceNow's business time left (the SLA's own schedule), else the hours
    to its planned end in servicenow_insights_business_hours (as the SLA at risk list). groups: only those (a page's
    groups); None = every group. None when the read was not asked."""
    o = opts or {}
    b = b or {}
    if not b.get("asked"):
        return None
    tz = _tz(b.get("time_zone") or o.get("time_zone"))
    now = _t(b.get("now")) or datetime.datetime.now(UTC)
    spec = business_hours_spec(o.get("business_hours"))
    win_h = float(_o(o, "breach_window_hours", 8))
    bands = breach_bands(win_h)
    days_n = max(7, int(_o(o, "breach_days", 30)))
    keep = (lambda g: True) if groups is None else (lambda g: g in groups)          # noqa: E731
    best = {}
    for x in b.get("open") or []:
        if not keep(x.get("group")):
            continue
        pe = _t(x.get("planned_end"))
        if x.get("left_s") is not None:
            left = x["left_s"] / 3600.0
        elif pe is not None:
            left = business_hours(now, pe, spec, tz) if pe > now else 0.0
        else:
            continue
        if left is None or left > win_h:
            continue
        row = dict(x, left_h=round(left, 4), left=fmt_left(left), band=next(bb for bb in bands if left <= bb),
                   nobody=bool(x.get("group")) and not x.get("assignee"), at=_breach_at(pe, now, tz),
                   kind=x.get("target") or sla_kind(x.get("sla"), o))
        k = x.get("task_id") or x.get("number")
        if k not in best or (row["left_h"], row.get("planned_end") or "") < (best[k]["left_h"], best[k].get("planned_end") or ""):
            best[k] = row
    queue = sorted(best.values(), key=lambda x: (x["left_h"], x.get("planned_end") or "", x.get("number") or ""))
    today = now.astimezone(tz).date()
    first = today - datetime.timedelta(days=days_n - 1)
    lead = first - datetime.timedelta(days=6)
    per = {}
    for x in b.get("breached") or []:
        pe = _t(x.get("planned_end"))
        if pe is None or pe > now or not keep(x.get("group")):
            continue
        d = pe.astimezone(tz).date()
        if d < lead or d > today:
            continue
        e_ = per.setdefault(d, {"n": 0, "response": 0, "resolution": 0, "other": 0, "groups": {}})
        e_["n"] += 1
        e_[x.get("target") or sla_kind(x.get("sla"), o)] += 1
        _count(e_["groups"], x.get("group") or "(no group)")

    def cnt(d, k="n"):
        return (per.get(d) or {}).get(k, 0)

    points = []
    for i in range(days_n):
        d = first + datetime.timedelta(days=i)
        points.append({"date": d.isoformat(), "n": cnt(d), "avg7": round(sum(cnt(d - datetime.timedelta(days=k)) for k in range(7)) / 7.0, 2),
                       "resolution": cnt(d, "resolution"), "response": cnt(d, "response")})
    weeks = []
    for w in range(3):
        end = today - datetime.timedelta(days=7 * w)
        start = end - datetime.timedelta(days=6)
        if start < lead:
            break
        ds = [start + datetime.timedelta(days=i) for i in range(7)]
        gs = {}
        for d in ds:
            for g, c_ in ((per.get(d) or {}).get("groups") or {}).items():
                _count(gs, g, c_)
        top = _top(gs, 1)[0] if gs else ""
        weeks.append({"label": ("Last 7 days (%s)" % _span(start, end)) if w == 0 else _span(start, end), "from": start.isoformat(), "to": end.isoformat(),
                      "n": sum(cnt(d) for d in ds), "resolution": sum(cnt(d, "resolution") for d in ds), "response": sum(cnt(d, "response") for d in ds),
                      "other": sum(cnt(d, "other") for d in ds), "worst": top, "worst_n": gs.get(top, 0) if top else 0, "groups": gs})
    tiles = []
    for i, bb in enumerate(bands):
        inb = [x for x in queue if x["left_h"] <= bb]
        note = ""
        if i == 0:
            nb = sum(1 for x in inb if x["nobody"])
            note = ("%d not assigned to anyone" % nb) if nb else "every one assigned"
        elif i < len(bands) - 1:
            note = "incl. the %d above" % sum(1 for x in queue if x["left_h"] <= bands[i - 1])
        else:
            note = "the rest of the window" if len(bands) > 1 else ""
        tiles.append({"key": "h%g" % bb, "label": "Breach within %s" % fmt_band(bb) if i == 0 else "Within %s" % fmt_band(bb), "value": len(inb),
                      "note": note, "status": "critical" if i == 0 and inb else ("warning" if i == 1 and inb else ("low" if inb else "ok"))})
    tiles.append({"key": "today", "label": "Breached today", "value": cnt(today), "note": "SLAs past their planned end today",
                  "status": "warning" if cnt(today) else "ok"})
    n1 = sum(1 for x in queue if x["left_h"] <= bands[0])
    one = lambda k, word: "%d %s%s" % (k, word, "" if k == 1 else "s")             # noqa: E731
    if not queue:
        head = "Nothing breaches in the next %s business hours" % ("%g" % win_h)
    elif n1:
        head = "%s breach%s within %s" % (one(n1, "ticket"), "es" if n1 == 1 else "", "the hour" if bands[0] == 1 else fmt_band(bands[0]))
    else:
        head = "%s breach%s within %s business hours" % (one(len(queue), "ticket"), "es" if len(queue) == 1 else "", "%g" % win_h)
    lines = []
    if len(weeks) >= 2:
        a_, b_ = weeks[0]["n"], weeks[1]["n"]
        head += "; breaches %s" % (("down from %d to %d this week" % (b_, a_)) if a_ < b_ else ("up from %d to %d this week" % (b_, a_)) if a_ > b_
                                   else "the same as the week before (%d)" % a_)
        lines.append("Last 7 days %d breaches, the 7 days before %d (%s%d)." % (a_, b_, "+" if a_ >= b_ else "-", abs(a_ - b_)))
        lines.append("Response SLAs: %d breached in the last 7 days, %d the week before." % (weeks[0]["response"], weeks[1]["response"]))
        if weeks[0]["worst"]:
            lines.append("%s has %d of the last 7 days' %d (the week before %d of %d)." % (
                weeks[0]["worst"], weeks[0]["worst_n"], a_, weeks[1]["groups"].get(weeks[0]["worst"], 0), b_))
    if points:
        a0, a1 = points[0]["avg7"], points[-1]["avg7"]
        lines.insert(0, "7-day average %.1f a day, %s %.1f on %s." % (
            a1, "down from" if a1 < a0 else "up from" if a1 > a0 else "the same as", a0, first.strftime("%d %b").lstrip("0")))
    if b.get("breached_error"):
        lines = ["The breached SLAs were not read: %s. No trend." % b["breached_error"]]
        points, weeks = [], []
        tiles[-1].update(value=None, note="not read", status="")
    notes = []
    if b.get("open_more"):
        notes.append("More running SLAs than servicenow_insights_max_records: the queue may be missing some.")
    if b.get("breached_more"):
        notes.append("More breached SLAs than servicenow_insights_max_records: the oldest days are short.")
    if b.get("vip_error"):
        notes.append("The VIP tiers were not read: %s." % b["vip_error"])
    vt = list(b.get("vip_tiers") or [])
    return {"on": True, "error": str(b.get("error") or ""), "window_h": win_h, "bands": bands, "head": head, "lines": lines, "notes": notes,
            "tiles": tiles, "queue": queue, "points": points, "weeks": weeks, "days": days_n, "today": today.isoformat(),
            "read_at": now.astimezone(tz).strftime("%d %b %Y %H:%M ") + (now.astimezone(tz).tzname() or ""),
            "vip_tiers": vt, "business_hours": business_hours_label(o.get("business_hours"), b.get("time_zone") or o.get("time_zone") or ""),
            "max_rows": int(_o(o, "breach_max_rows", 50)),
            "breached": [dict(x, at=_t(x.get("planned_end")).astimezone(tz).strftime("%Y-%m-%d %H:%M"), kind=x.get("target") or sla_kind(x.get("sla"), o))
                         for x in b.get("breached") or [] if keep(x.get("group")) and _t(x.get("planned_end")) and lead <= _t(x["planned_end"]).astimezone(tz).date() <= today
                         and _t(x["planned_end"]) <= now] if groups is None else []}


BREACH_EMAIL_ROWS = 25


def breach_email(bm, rows_max=BREACH_EMAIL_ROWS):
    """1.59.0: the email's About to breach - the tiles as one row, the queue (no description: the email leaves the site),
    the weeks side by side. Numbers only: the web page draws the line."""
    if not bm:
        return []
    if bm.get("error"):
        return [{"title": "About to breach", "status": "warning", "text": "The running SLAs were not read: %s." % bm["error"]}]
    q = bm.get("queue") or []
    bands = bm.get("bands") or [8.0]
    st = {0: "critical", 1: "warning"} if len(bands) == 3 else {0: "critical"}
    t = bm.get("tiles") or []
    out = [{"title": "About to breach", "status": "critical" if t and t[0]["value"] else ("warning" if q else "ok"),
            "text": "%s. The next %g business hours, read %s." % (bm.get("head") or "", bm.get("window_h", 8), bm.get("read_at") or ""),
            "columns": [x["label"] for x in t], "rows": [["—" if x["value"] is None else x["value"] for x in t]]}]
    if q:
        more = len(q) - rows_max
        out.append({"title": "About to breach, least time left first",
                    "text": " ".join(['"nobody": a group but no person.'] + (["%d more on the web page and in the workbook." % more] if more > 0 else [])),
                    "columns": ["Time left", "Ticket", "Prio", "Group", "Assigned to", "SLA used", "Breaches at"],
                    "rows": [[x.get("left"), x.get("number"), ("P" + x["priority_value"]) if x.get("priority_value") else x.get("priority") or "",
                              x.get("group") or "", "nobody" if x.get("nobody") else x.get("assignee") or "",
                              ("%d%%" % round(x["pct"])) if x.get("pct") is not None else "", x.get("at")] for x in q[:rows_max]],
                    "row_status": [st.get(bands.index(x["band"]) if x.get("band") in bands else -1, "") for x in q[:rows_max]]})
    if bm.get("weeks") or bm.get("lines"):
        out.append({"title": "Breaches, last %d days" % int(bm.get("days") or 30), "text": " ".join(bm.get("lines") or []),
                    "columns": ["Week", "Breached", "Resolution", "Response", "Worst group"],
                    "rows": [[w["label"], w["n"], w["resolution"], w["response"], ("%s (%d)" % (w["worst"], w["worst_n"])) if w["worst"] else ""]
                             for w in bm.get("weeks") or []]})
    return out


def breach_xlsx(bm):
    """1.59.0: the workbook's sheets - every ticket about to breach, every day's breaches, every breached SLA read."""
    if not bm or bm.get("error"):
        return []
    vt = bm.get("vip_tiers") or []
    out = [{"name": "About to breach", "columns": ["Time left (business hours)", "Time left", "Ticket", "Priority", "Short description", "Group", "Assigned to",
                                                  "SLA", "Kind", "SLA used %", "Breaches at"] + (["VIP"] if vt else []),
            "rows": [[round(x["left_h"], 2), x["left"], x.get("number"), x.get("priority"), x.get("short"), x.get("group"),
                      "nobody" if x.get("nobody") else x.get("assignee") or "", x.get("sla"), x.get("kind"), x.get("pct"), x.get("at")] +
                     ([vt[x["vip"]] if x.get("vip") is not None and x["vip"] < len(vt) else ""] if vt else []) for x in bm.get("queue") or []]}]
    if bm.get("points"):
        out.append({"name": "Breaches by day", "columns": ["Day", "Breached", "Resolution", "Response", "7-day average"],
                    "rows": [[p_["date"], p_["n"], p_.get("resolution", ""), p_.get("response", ""), p_["avg7"]] for p_ in bm["points"]]})
    if bm.get("breached"):
        out.append({"name": "Breached SLAs", "columns": ["Breached at", "Ticket", "Priority", "Group", "Assigned to", "SLA", "Kind"],
                    "rows": [[x["at"], x.get("number"), x.get("priority"), x.get("group"), x.get("assignee"), x.get("sla"), x.get("kind")]
                             for x in sorted(bm["breached"], key=lambda x: x["at"], reverse=True)]})
    return out


def servicenow_insights_breach_only(r, opts=None):
    """1.59.0, servicenow_insights_mode breach: the About to breach of every group read (the module's breach_only answer)."""
    r = r or {}
    if not r.get("breach"):
        return {"on": True, "error": str(r.get("error") or "ServiceNow was not read"), "queue": [], "tiles": []}
    return breach_model(r["breach"], opts)


def servicenow_insights_breach_doc(bm, opts=None):
    """1.59.0, servicenow_insights_mode breach: the email and the website's index line - the tiles as the summary, then the
    queue and the weeks (breach_email)."""
    bm = bm or {}
    t = bm.get("tiles") or []
    secs = breach_email(bm)
    status = secs[0]["status"] if secs else "info"
    return {"title": "About to breach: %s" % (bm.get("head") or bm.get("error") or "ServiceNow was not read"),
            "subtitle": "The next %g business hours, read %s" % (bm.get("window_h", 8), bm.get("read_at") or ""),
            "status": status, "summary": [{"label": x["label"], "value": "—" if x["value"] is None else x["value"], "note": x["note"],
                                            "status": {"critical": "critical", "warning": "warning"}.get(x["status"], "")} for x in t],
            "sections": secs[1:] if len(secs) > 1 and not bm.get("error") else secs}


def servicenow_insights_breach_xlsx(bm):
    return breach_xlsx(bm)


def _breach_r(r, groups):
    """r's breach rows of these groups only (the split's halves)."""
    b = (r or {}).get("breach")
    if not b:
        return None
    return dict(b, open=[x for x in b.get("open") or [] if x.get("group") in groups],
                breached=[x for x in b.get("breached") or [] if x.get("group") in groups])


def _scope(model, key):
    return (model or {}).get("scopes", {}).get(key or "all") or (model or {}).get("scopes", {}).get("all")


def servicenow_insights_doc(model, key="all", opts=None):
    """The report site_email sends (and prints, and lists on the website's index): the tiles and the main tables. The web
    page has every section."""
    o = opts or {}
    s = _scope(model, key)
    win = model.get("window") or {}
    c = s["counts"]
    b = s["backlog"]
    slo = model.get("slo") or {}
    two = slo.get("source") == "task_sla" or bool(slo.get("response_measured"))       # Response met and Resolution met (1.7.0)

    def pf(v):
        return ("%.1f%%" % v) if v is not None else "—"
    behind = b[0] > b[1] * 1.05 and b[0] - b[1] >= 5
    status = "info" if not c["incidents"] and not c["requests"] else ("warning" if behind or s["age"][3] else "ok")
    top = s["issues"][0] if s["issues"] else None
    title = "Ticket insights for %s: %s incidents, %s requests%s, backlog %d (%s)" % (
        s["name"], "{:,}".format(c["incidents"]), "{:,}".format(c["requests"]),
        (", top issue %s (%d)" % (top["name"], top["count"])) if top else "", b[0], _change(b[0], b[1]) or "no change")
    summary = [{"label": "Incidents", "value": c["incidents"], "note": "was %d" % c["incidents_prev"] if win.get("compare") else ""},
               {"label": "Requests", "value": c["requests"]},
               {"label": "Open now", "value": b[0], "status": "warning" if behind else "", "note": "was %d at the start" % b[1]},
               {"label": "Median time to resolve", "value": fmt_hours(s["median_h"][0])},
               ] + ([{"label": "Response met", "value": pf(s["resp"][0])}, {"label": "Resolution met", "value": pf(s["sla"][0])}] if two else
                    [{"label": "SLAs met", "value": pf(s["sla"][0])}]) + [
               {"label": "Close notes that say what was done", "value": "%d%%" % s["quality"][0]}]
    sv = s.get("survey") or {}
    if sv.get("n") and sv.get("avg") is not None:          # 1.16.0: the headline survey on its scale, graded - the word always there
        top = (sv.get("scale") or [None, None])[1]
        summary.append({"label": "Satisfaction", "value": ("%.2f of %g" % (sv["avg"], top)) if top is not None else "%.2f" % sv["avg"],
                        "status": {"Good": "ok", "Fair": "warning", "Low": "critical"}.get(sv.get("grade"), ""),
                        "note": " · ".join(x for x in (sv.get("grade") or "", sv.get("label") or "", "%d answers" % sv["n"]) if x)})
    secs = breach_email(s.get("breach")) + [
        {"title": "What the period says" + (" (written by ACT)" if s.get("summary_by") == "act" else ""), "lines": s["summary"] or ["(nothing to say)"]}]
    # 1.7.0: what to act on first - the counts, and the first of each list
    hp, hd, cd, cl = s.get("hops") or {}, s.get("hold") or {}, s.get("codes") or {}, s.get("closure") or {}
    if hp or hd or cd or cl:
        lim, cdays = int(hp.get("limit") or 3), hd.get("caller_days") or 6
        rk = s.get("at_risk") or {}
        # the email leaves the site: the short descriptions with the names, numbers, addresses and hosts replaced
        acts = [("SLA at risk now (past %g%%, or breached and open)" % (rk.get("pct") or 75), rk.get("count") or 0,
                 ["%s %s, %s %s, %s, %s" % (x["number"], x["priority"], x["sla"], "breached" if x["breached"] else ("%.0f%% used, %s left" % (x["pct"] or 0, fmt_hours(x["left_h"]))),
                                           x["group"], x["assignee"] or "unassigned") for x in (rk.get("rows") or [])[:5]]),
                ("Open over %d hops" % lim, hp.get("open") or 0, ["%s %s, %s hops, in %s, %g days" % (x["number"], x.get("what_m", ""), x["hops"], x["group"], x["age"])
                                                                 for x in (hp.get("rows") or [])[:5]]),
                ("Awaiting Caller over %g business days" % cdays, hd.get("late_n") or 0,
                 ["%s %s, %s, on hold since %s (%s business days)" % (x["number"], x.get("what_m", ""), x["group"], x["since"], x["days"]) for x in (hd.get("late") or [])[:5]]),
                ("%s in the period" % (cd.get("review") or "No Resolution Provided"), cd.get("review_n") or 0,
                 ["%s %s, %s, resolved %s" % (x["number"], x.get("what_m", ""), x["group"], x["resolved"]) for x in (cd.get("review_rows") or [])[:5]]),
                ("Resolved, not closed after %g days" % (cl.get("days") or 7), cl.get("count") or 0,
                 ["%s %s, resolved %s" % (x["number"], x["group"], x["resolved"]) for x in (cl.get("rows") or [])[:5]])]
        lines = []
        for lbl, cnt, first in acts:
            lines.append("%s: %d" % (lbl, cnt))
            lines += ["  - " + x for x in first]
            if cnt > len(first):
                lines.append("  - ... and %d more (the web page lists them all)" % (cnt - len(first)))
        secs.append({"title": "Act on", "status": "warning" if any(cnt for _, cnt, _ in acts) else "ok", "lines": lines})
    side = (s.get("side") or {}).get("rows") or (s.get("teams") if key in (None, "all") else None)
    if side:
        kind = (s.get("side") or {}).get("kind") or "Teams"
        ctc = s.get("ctasks") is not None                 # change_task read (1.2.0): its two columns
        # 1.18.0: Open now with what it was, and the change tasks closed / open, each in one column - the table fits an
        # email 800 px wide (it was 12 columns)
        secs.append({"title": "%s side by side" % kind, "columns": [kind[:-1] if kind != "Directorates" else "Directorate", "Incidents", "Open now (was)",
                                                               "Over %d days" % int(_o(o, "old_days", 30)), "Median fix"] +
                                (["Response met", "Resolution met"] if two else ["SLA met"]) + ["Bare notes"] +
                                (["Change tasks closed / open"] if ctc else []) + ["Top issue"],
                     "rows": [[t.get("label") or t["team"], t["incidents"], "{:,} ({:,})".format(t["backlog"][0], t["backlog"][1]), t["old"], fmt_hours(t["median_h"])] +
                              ([pf(t.get("resp"))] if two else []) + [pf(t["sla"]), "%d%%" % t["quality"][2]] +
                              (["{:,} / {:,}".format(t.get("ct_closed") or 0, t.get("ct_open") or 0)] if ctc else []) +
                              [("%s (%d)" % (t["top"][0], t["top"][1])) if t["top"][0] else ""] for t in side],
                     "row_status": ["warning" if t["backlog"][0] > t["backlog"][1] * 1.05 and t["backlog"][0] - t["backlog"][1] >= 5 else ""
                                    for t in side]})
    src = {"tix": "from the tickets", "mix": "partly from tickets", "ai": "suggested by ACT", "none": "no fix in the notes"}
    n = int(_o(o, "top_issues", 10))
    if s["issues"]:
        secs.append({"title": "Top incident issues", "columns": ["#", "Rank", "Issue", "Incidents", "Was", "Median fix", "Bare notes", "Fix"],
                     "rows": [[i["rank"], i["mv"]["text"], i["name"], i["count"], i["prev"], fmt_hours(i["median_h"]), "%d%%" % i["quality"][2],
                               src.get(i["fix"]["source"], "")] for i in s["issues"][:n]]})
    # 1.7.0: by priority with your SLAs, the priority set by hand, the categories
    if s["prio"]:
        secs.append({"title": "By priority", "text": "Met or missed: %s." % {"task_sla": "ServiceNow's SLAs (task_sla)", "targets": "your targets, " + (
                         slo.get("business_hours") or "calendar hours"), "made_sla": "ServiceNow's Made SLA"}.get(slo.get("source") or "targets", ""),
                     "columns": ["Priority", "Incidents", "Median to resolve"] + (["Response met", "Resolution met"] if two else ["SLA met"]) + ["No SLA"],
                     "rows": [[p["label"], p["count"], fmt_hours(p["median_h"])] + ([pf(p.get("resp"))] if two else []) + [pf(p["sla"]), p.get("no_slo") or 0]
                              for p in s["prio"]]})
    vsec = vip_email(model, s, side, two, pf)
    if vsec:
        secs += vsec
    mx = s.get("matrix") or {}
    if mx.get("on") and mx.get("checked"):
        secs.append({"title": "Priority set by hand", "status": "warning" if (mx.get("pct") or 0) >= 10 else "",
                     "text": "%d of %d incidents (%s) have a priority the 3 x 3 matrix does not give for their impact and urgency. %d were set higher, %d lower." % (
                         mx["off"], mx["checked"], pf(mx["pct"]), mx.get("higher") or 0, mx.get("lower") or 0),
                     "columns": ["Group", "Set by hand", "Of", "Share"],
                     "rows": [[g["group"], g["off"], g["of"], pf(g["pct"])] for g in mx.get("by_group") or []]})
    oc = s.get("outcomes") or {}
    if oc.get("by_group"):
        secs.append({"title": "How incidents ended", "text": "Unable to resolve (%s): %s, the window before %s. First-contact resolution: %s." % (
                         " + ".join(oc.get("unable_names") or []), pf(oc["unable"][0]), pf(oc["unable"][1]), pf((s.get("fcr") or {}).get("now"))),
                     "columns": ["Group", "Resolved"] + oc["names"] + ["Unable to resolve"],
                     "rows": [[g["group"], g["resolved"]] + [g["counts"].get(k_, 0) for k_ in oc["names"]] + [pf(g["unable"])] for g in oc["by_group"]]})
    # 1.9.0: the time in each state and the backlog's age, per group
    lc, ag = s.get("lifecycle") or {}, s.get("aging") or {}
    if lc.get("measured"):
        secs.append({"title": "Time in each state", "text": "Average hours per state, for the incidents resolved in the period. On hold: %s of the time to "
                                                            "resolve, %d incidents more than %d times." % (
                                                                pf(lc.get("hold_share")), lc.get("held_many") or 0, lc.get("hold_many") or 2),
                     "columns": ["Group", "Incidents", "Waiting to start", "In progress", "On hold", "Resolved to closed"],
                     "rows": [["All", lc["n"]] + [fmt_hours(lc["avg"].get(k_)) for k_ in STATE_KINDS]] +
                             [[g["group"], g["n"]] + [fmt_hours(g[k_]) for k_ in STATE_KINDS] for g in lc.get("by_group") or []]})
    if ag.get("by_group"):
        secs.append({"title": "Backlog age (business days)", "columns": ["Group", "Open now"] + ag["buckets"],
                     "rows": [[g["group"], g["open"]] + g["counts"] for g in ag["by_group"]]})
    cat = s.get("categories") or {}
    if cat.get("rows"):
        dq = "%d with no category (%s), %d in Other (%s)" % (cat["empty"], pf(cat["empty_pct"]), cat["other"], pf(cat["other_pct"]))
        if cat.get("listed"):
            dq += ", %d not on the official list (%s)%s" % (cat["off_list"], pf(cat["off_pct"]),
                                                            (": " + ", ".join("%s (%d)" % (nm, c_) for nm, c_ in cat["off_names"])) if cat["off_names"] else "")
        secs.append({"title": "Categories", "text": "Data quality: %s." % dq,
                     "columns": ["Category", "Incidents", "Share", "Median to resolve"] + (["Response met", "Resolution met"] if two else ["SLA met"]) + ["Mostly in"],
                     "rows": [[x["name"], x["count"], pf(x["share"]), fmt_hours(x["median_h"])] + ([pf(x.get("resp"))] if two else []) + [pf(x.get("resol")), x.get("team") or ""]
                              for x in cat["rows"][:20]],
                     "row_status": ["warning" if x.get("official") is False else "" for x in cat["rows"][:20]]})
    if s["requests"]:
        secs.append({"title": "Most requested services", "columns": ["#", "Rank", "Catalog item", "Requests", "Was", "Median to fulfil", "SLA met"],
                     "rows": [[q["rank"], q["mv"]["text"], q["name"], q["count"], q["prev"],
                               ("%.1f days" % q["median_days"]) if q["median_days"] is not None else "—",
                               ("%.1f%%" % q["sla"]) if q["sla"] is not None else "—"] for q in s["requests"][:int(_o(o, "top_requests", 10))]]})
    if s["majors"]:
        secs.append({"title": "P1 and P2 incidents", "status": "critical" if any(m["priority"] == "P1" for m in s["majors"]) else "warning",
                     "columns": ["Incident", "Opened", "Priority", "What happened", "Group", "To resolve", "Caused by change", "Problem"],
                     "rows": [[m["number"], m["opened"], m["priority"], m["what"], m["group"], fmt_hours(m["hours"]), m["change"], m["problem"]]
                              for m in s["majors"][:30]]})
    if s["problems"]:
        secs.append({"title": "Problem candidates", "columns": ["Issue", "CI", "Last %d days" % int(_o(o, "problem_days", 30)), "In the period", "Group", "Problem record"],
                     "rows": [[p["issue"], p["ci"], p["last"], p["count"], p["group"],
                               (p["problem"] + (" (" + p["problem_state"] + ")" if p["problem_state"] else "") +
                                (": resolved, incidents continue" if p["continues"] else "")) if p["problem"] else "none"] for p in s["problems"][:10]],
                     "row_status": ["warning" if not p["problem"] or p["continues"] else "" for p in s["problems"][:10]]})
    if s["changes"]["rows"]:
        secs.append({"title": "Changes failed or backed out", "columns": ["Change", "Closed", "What", "Group", "Outcome", "Incidents that followed"],
                     "rows": [[x["number"], x["closed"], x["what"], x["group"], x["outcome"],
                               ", ".join("%s (%s)" % (f[0], "caused by" if f[1] == "link" else "same CI") for f in x["followed"]) or "none"]
                              for x in s["changes"]["rows"][:20]]})
    ct = s.get("ctasks")
    if ct and (ct["closed"] or ct["open"]):
        ch = s["changes"]
        lines = ["Closed: %d (the window before: %d), median %s to close" % (ct["closed"], ct["closed_prev"], fmt_hours(ct["median_h"])),
                 "  - by the planned end %d, after it %d, with no planned end %d" % (ct["on_time"], ct["overdue"], ct["no_end"]),
                 "Open now: %d, %d past the planned end, %d older than %d days" % (ct["open"], ct["late_open"], ct["age"][3], int(_o(o, "old_days", 30)))]
        if ch.get("with_tasks"):
            lines.append("Changes closed with tasks: %d of %d, %s tasks each on average (most: %s, %d)" % (
                ch["with_tasks"], ch["count"], ch["per_change"], ch["most"][0], ch["most"][1]))
        sec = {"title": "Change tasks", "lines": lines}
        if ch.get("held_by"):
            sec.update(text="Held up by: the team whose task a change waited on last. The wait runs from the other tasks' close "
                            "(one task: the work start) to that task's close.",
                       columns=["Team", "Changes held up", "Median wait"],
                       rows=[[x["group"], x["count"], fmt_hours(x["median_h"])] for x in ch["held_by"]])
        secs.append(sec)
    nr = not_read_lines(model)
    if nr:
        secs.append({"title": "Not read", "status": "warning", "lines": nr})
    if model.get("cut"):                    # 1.16.0: first, before every section
        secs.insert(0, {"title": "Records cut: some figures are incomplete", "status": "critical", "lines": list(model["cut"])})
    elif model.get("compare_cut"):
        secs.insert(0, {"title": "No compare this run", "status": "warning", "lines": [model["compare_cut"]]})
    secs.append({"title": "Read", "lines": ["%s: %d record(s) kept of %d read" % (t["table"], t["kept"], t["read"]) for t in model.get("tables") or [] if not t.get("error")] +
                 ["Window: %s, compared with %s (%s)" % (win.get("label"), win.get("prev_label") or "nothing", win.get("time_zone") or "UTC")] +
                 ["ACT: %s" % act_line(model)]})
    return {"title": title, "subtitle": "%s - %s%s" % (s["name"], win.get("label") or "", (" · Group manager: " + s["lead"]) if s.get("lead") else ""), "status": status, "summary": summary, "sections": secs,
            "footer": "ServiceNow %s" % (model.get("url") or "")}


VIP_EMAIL_COLS = 8                 # the by-unit table in the email while it has at most this many columns (800 px wide)


def vip_email(model, s, side, two, pf):
    """1.43.0: the email's VIP sections - the scope's table (a row per tier and Not VIP), and on a page with units below
    it the VIP tickets by unit: a table while it fits the email (VIP_EMAIL_COLS columns), else one line per unit."""
    v, mv = s.get("vip"), model.get("vip") or {}
    if v is None or not mv.get("on"):
        return []
    tiers = mv.get("tiers") or []
    notes = []
    if any(r_["small"] for r_ in v["rows"]):
        notes.append("Fewer than %d tickets: counts only." % int(mv.get("min") or 5))
    if mv.get("error"):
        notes.append("The VIP tiers were not read: %s. Every ticket counts as Not VIP." % mv["error"])
    if mv.get("hidden"):
        notes.append("Not readable on sys_user: %s. That tier counts no one." % ", ".join(mv["hidden"]))
    out = [{"title": "VIP tickets", "text": " ".join(["Opened in the window, by the VIP tier of who they were raised for."] + notes),
            "columns": ["Tier", "Incidents", "Requests", "Open now", "Median to resolve"] + (["Response met", "Resolution met"] if two else ["SLA met"]) + ["No SLA"],
            "rows": [[r_["label"], r_["incidents"], r_["requests"], r_["open"], fmt_hours(r_["median_h"])] + ([pf(r_["resp"])] if two else []) +
                     [pf(r_["resol"]), r_["no_slo"]] for r_ in v["rows"]]}]
    units = [t for t in side or [] if t.get("vip")]
    if units and s.get("level") != "team":
        cols = ["Unit"] + tiers + ["Open now"] + (["Response met"] if two else []) + ["Resolution met" if two else "SLA met", "Median to resolve"]
        if len(cols) <= VIP_EMAIL_COLS:
            out.append({"title": "VIP tickets by unit", "columns": cols,
                        "rows": [[t.get("label") or t["team"]] + list(t["vip"]["n"]) + [t["vip"]["open"]] + ([pf(t["vip"]["resp"])] if two else []) +
                                 [pf(t["vip"]["resol"]), fmt_hours(t["vip"]["median_h"])] for t in units]})
        else:
            out.append({"title": "VIP tickets by unit", "lines": ["%s: %s, open now %d, %s met %s" % (
                t.get("label") or t["team"], ", ".join("%s %d" % (lb, x) for lb, x in zip(tiers, t["vip"]["n"])), t["vip"]["open"],
                "resolution" if two else "SLA", pf(t["vip"]["resol"])) for t in units]})
    return out


def act_line(model):
    a = model.get("act") or {}
    if not a.get("on"):
        return "off (servicenow_insights_act): the issues are the incidents' categories, the fixes their most common close notes"
    if not a.get("ran"):
        return "did not run (%s): the issues are the incidents' categories, the fixes their most common close notes" % (
            a.get("reason") or "; ".join(a.get("errors") or []) or "no answer")
    return "%d of %d questions answered (%s%s); %d issues, %d fixes written%s" % (
        a.get("answered", 0), a.get("asked", 0), a.get("provider") or "ACT", (" " + a["model"]) if a.get("model") else "",
        a.get("issues", 0), a.get("fixes", 0), ("; not answered: " + "; ".join(a["errors"][:3])) if a.get("errors") else "")


def cut_lines(r):
    """1.16.0: a table over servicenow_insights_max_records keeps its newest records. The window cut too (the read of its
    older records capped as well): one line a table, for the top of every page and the email. The date is where the read
    stopped: a ticket opened before it is missing from every month it counts in (resolved, open, ...)."""
    out = []
    for t in (r or {}).get("tables") or []:
        if t.get("window_cut"):
            tot, n_ = t.get("total"), "{:,}".format(int(t.get("read") or 0))
            out.append("%s: only the newest %s read, back to %s%s. Figures in this report are incomplete: raise "
                       "servicenow_insights_max_records." % (t["table"], ("%s of %s" % (n_, "{:,}".format(int(tot)))) if tot else n_,
                                                             str(t.get("cut") or "")[:10], "" if tot else ", and there are more"))
    return out


def compare_cut_line(cc):
    return ("The window before was cut (%s: more than servicenow_insights_max_records): no compare this run. The window's "
            "figures are whole." % ", ".join(cc)) if cc else ""


def not_read_lines(model):
    out = []
    for t in model.get("tables") or []:
        if t.get("error"):
            out.append("%s: %s%s" % (t["table"], t["error"], " - the change tasks are left out (their columns, Held up by, the workbook's sheet)"
                                     if t["table"] == "change_task" else ""))
        elif t.get("more") and not t.get("window_cut"):
            out.append("%s: more than servicenow_insights_max_records - the newest kept, the window before cut at %s: no compare" % (
                t["table"], str(t.get("cut") or "")[:10]))
    for g in model.get("groups") or []:
        if g.get("found") is False:
            out.append("assignment group %s is not in ServiceNow" % g["name"])
    if model.get("audit_error"):
        err = model["audit_error"]
        out.append("%s (the hand-off paths): %s - the counts are there, the paths are not" % (
            "sys_audit and metric_instance" if "metric_instance:" in err else "sys_audit", err.replace("sys_audit: ", "", 1) if "metric_instance:" not in err else err))
    if model.get("audit_note"):
        out.append("the hand-off paths: %s" % model["audit_note"])
    if model.get("members_error"):
        out.append("sys_user_grmember (the teams' members): %s - each leaderboard lists everyone the team's tickets were assigned to or "
                   "resolved by instead" % model["members_error"])
    if model.get("leads_error"):
        out.append("sys_user_group.manager (the team leads): %s - no Group manager line, no lead emailed" % model["leads_error"])
    if model.get("group_tree_error"):
        out.append("sys_user_group (the groups' parents, for the sites and directorates): %s - placed by their names instead"
                   % model["group_tree_error"])
    if ((model.get("act") or {}).get("work_notes") or {}).get("error"):
        out.append("sys_journal_field (the incidents' work notes, for ACT's fixes): %s - the fixes were written from the close notes "
                   "only" % model["act"]["work_notes"]["error"])
    slo, pr = model.get("slo") or {}, model.get("process") or {}
    if slo.get("note"):
        out.append(slo["note"])
    if slo.get("open_cut"):
        out.append("task_sla of the open incidents: %d of %d read (servicenow_insights_max_records) - SLA at risk now may miss some" % tuple(slo["open_cut"]))
    if slo.get("first_assigned_error"):
        out.append("metric_instance (Assigned to, the response from your targets): %s - the response is not measured" % slo["first_assigned_error"])
    if pr.get("states_error") and pr["states_error"] != "none":
        out.append("metric_instance (%s, the time on hold): %s - %s" % (pr.get("state_metric") or "the state metric", pr["states_error"],
                   "on hold since from task_sla's pause instead" if not pr.get("paused_error") else "the time on hold is not known"))
    if pr.get("survey_error"):
        out.append("asmt_metric_result (the incident surveys): %s - no satisfaction figures" % pr["survey_error"])
    if model.get("lookup_note"):
        out.append(model["lookup_note"])
    if (model.get("window") or {}).get("note"):
        out.append(model["window"]["note"])
    return out


def servicenow_insights_xlsx(model, r=None, state=None, mask=None, opts=None):
    """The workbook: every incident with its issue and close-note grade, then the issues, requests, changes, people, teams."""
    o = opts or {}
    r = r or {}
    s = _scope(model, "all")
    tz = _tz((model.get("window") or {}).get("time_zone"))
    since, until, _ = _window(r)

    def when(t):
        d = _t(t)
        return d.astimezone(tz).strftime("%Y-%m-%d %H:%M") if d else ""

    issues_of = _assign(r, state, mask, o)
    hier = model.get("hier") or {}

    sp = model.get("split") or {}
    split_col = sp.get("label") or "Split"
    split_groups = set(sp.get("groups") or []) if sp else None

    def place(g, k):
        return (hier.get(g) or hier_place(g, o) if g else {}).get(k, "")
    inc = [dict(x) for x in r.get("items") or [] if x.get("table") == "incident" and _in(_t(x.get("opened_at")), since, until)]
    apply_slas(inc, r, o, tz)
    yn = lambda v: "" if v is None else ("yes" if v else "no")          # noqa: E731
    sheets = [{"name": "Incidents", "columns": ["Number", "Short description", "Issue", "Category", "Subcategory", "Priority", "State",
                                                "Site", "Directorate", "Placed by", split_col, "Assignment group", "Assigned to", "Resolved by", "Caller", "Configuration item", "Opened",
                                                "Resolved", "Hours to resolve", "Reassignments", "Reopened", "Made SLA", "SLA target (hours)", "Close code",
                                                "Close note grade", "Close notes", "Caused by", "Problem",
                                                # 1.7.0
                                                "Response met", "No SLA", "SLAs", "Impact", "Urgency", "Priority by the matrix"],
               "rows": [[x.get("number"), x.get("short_description"), issues_of.get(x["sys_id"]) or category_issue(x), x.get("category"),
                         x.get("subcategory"), x.get("priority"), x.get("state"),
                         place(x.get("group"), "site"), place(x.get("group"), "dir"), place(x.get("group"), "by"),
                         ("yes" if x.get("group") in split_groups else "no") if split_groups is not None else "",
                         x.get("group"), x.get("assigned_to"), x.get("resolved_by"),
                         x.get("caller"), x.get("cmdb_ci"), when(x.get("opened_at")), when(x.get("done_at")), _r1(_hours(x)),
                         x.get("reassignment_count"), x.get("reopen_count"), yn(x.get("_resol")), x.get("sla_target_h", ""), x.get("close_code"), grade(x.get("close_notes"), o) if x.get("done_at") else "", x.get("close_notes"),
                         x.get("caused_by"), x.get("problem"),
                         yn(x.get("_resp")), "yes" if x.get("_no_slo") else "", "; ".join("%s (%s)" % (nm, k_) for nm, k_ in x.get("_slas") or []),
                         x.get("impact_value"), x.get("urgency_value"), matrix_priority(x, o)] for x in inc]}]
    if split_groups is None:                   # no split: no column for it
        k_ = sheets[0]["columns"].index(split_col)
        del sheets[0]["columns"][k_]
        for row in sheets[0]["rows"]:
            del row[k_]
    src = {"tix": "from the tickets", "mix": "partly from tickets", "ai": "suggested by ACT", "none": ""}
    sheets.append({"name": "Issues", "columns": ["Rank", "Issue", "Incidents", "Window before", "Share %", "Median hours", "Good %", "Thin %",
                                                 "Bare %", "Mostly in", "CIs", "Fix from", "Fix"],
                   "rows": [[i["rank"], i["name"], i["count"], i["prev"], i["share"], i["median_h"], i["quality"][0], i["quality"][1], i["quality"][2],
                             i["main_group"], ", ".join(i["cis"]), src.get(i["fix"]["source"], ""), " | ".join(i["fix"]["steps"])] for i in s["issues"]]})
    sheets.append({"name": "Requests", "columns": ["Rank", "Catalog item", "Requests", "Window before", "Median days", "SLA met %"],
                   "rows": [[q["rank"], q["name"], q["count"], q["prev"], q["median_days"], q["sla"]] for q in s["requests"]]})
    cats = s.get("categories") or {}
    sheets.append({"name": "Categories", "columns": ["Category", "Incidents", "Window before", "Share %", "Median hours", "Response met %",
                                                     "Resolution met %", "Mostly in", "On the official list"],
                   "rows": [[c["name"], c["count"], c["prev"], c["share"], c["median_h"], c["resp"], c["resol"], c["team"],
                             "" if c["official"] is None else ("yes" if c["official"] else "no")] for c in cats.get("rows") or []]})
    pr = s.get("prio") or []
    sheets.append({"name": "Priorities", "columns": ["Priority", "Incidents", "Median hours", "Response target (hours)", "Response met %",
                                                     "Resolution target (hours)", "Resolution met %", "No SLA"],
                   "rows": [[p["label"], p["count"], p["median_h"], p.get("resp_target_h"), p.get("resp"), p.get("target_h"), p["sla"], p.get("no_slo")]
                            for p in pr]})
    sheets.append({"name": "Changes failed", "columns": ["Change", "Closed", "What", "Group", "Outcome", "Followed by"],
                   "rows": [[x["number"], x["closed"], x["what"], x["group"], x["outcome"], ", ".join(f[0] + (" (caused by)" if f[1] == "link" else " (same CI)") for f in x["followed"])]
                            for x in s["changes"]["rows"]]})
    if s.get("ctasks") is not None:
        # every change task opened, closed or open in the window (1.2.0)
        def ontime(x):
            d, pe = _t(x.get("done_at")), _t(x.get("planned_end"))
            if not pe:
                return "no planned end"
            if d:
                return "yes" if d <= pe else "no"
            return "open, past the planned end" if pe < until else "open"
        cts = [x for x in r.get("items") or [] if x.get("table") == "change_task" and (
            _in(_t(x.get("opened_at")), since, until) or _in(_t(x.get("done_at")), since, until) or
            (_t(x.get("opened_at")) and _t(x["opened_at"]) <= until and (not _t(x.get("done_at")) or _t(x["done_at"]) > until)))]
        sheets.append({"name": "Change tasks", "columns": ["Number", "Change", "Short description", "State", "Site", "Directorate"] +
                       ([split_col] if split_groups is not None else []) + ["Assignment group", "Assigned to", "Closed by", "Opened",
                                                                            "Planned start", "Planned end", "Closed", "Hours to close",
                                                                            "By the planned end", "Close code", "Close notes"],
                       "rows": [[x.get("number"), x.get("parent"), x.get("short_description"), x.get("state"), place(x.get("group"), "site"),
                                 place(x.get("group"), "dir")] + ([("yes" if x.get("group") in split_groups else "no")] if split_groups is not None else []) +
                                [x.get("group"), x.get("assigned_to"), x.get("closed_by"), when(x.get("opened_at")), when(x.get("planned_start")),
                                 when(x.get("planned_end")), when(x.get("done_at")), _r1(_hours(x)), ontime(x), x.get("close_code"), x.get("close_notes")]
                                for x in sorted(cts, key=lambda x: x.get("opened_at") or "")]})
    sheets.append({"name": "Problem candidates", "columns": ["Issue", "CI", "Last days", "In period", "Group", "Problem", "State", "Incidents continue"],
                   "rows": [[p["issue"], p["ci"], p["last"], p["count"], p["group"], p["problem"], p["problem_state"], "yes" if p["continues"] else ""]
                            for p in s["problems"]]})
    people = []
    for t in model.get("teams") or []:
        st = model["scopes"][t].get("stars") or {}
        for p in st.get("people") or []:
            people.append([t, p["name"], "member" if st.get("members_known") else "worked its tickets", p["resolved"], p.get("ctask") or 0, p["score"], p["median_h"],
                           p["quality"][0], p["quality"][2], p["repeat"], p["opened"]])
        if (st.get("outside") or {}).get("count"):
            people.append([t, "(closed by people outside the team: %s)" % ", ".join(st["outside"]["names"]), "outside", st["outside"]["count"],
                           "", "", "", "", "", "", ""])
    sheets.append({"name": "People", "columns": ["Team", "Person", "In the team", "Closed", "Of them change tasks", "Score", "Median hours", "Good notes %",
                                                 "Bare notes %", "Reopened %" if model.get("reopen_used") else "Came back %", "Opened"], "rows": people})
    # Manager (1.18.0): the group's manager as its team page shows it (servicenow_insights_show_lead); empty without one
    show = o.get("show_lead", True) not in (False, "false", "False", 0)
    lead_of = lambda g: lead_label(model["leads"][g]) if show and (model.get("leads") or {}).get(g) else ""     # noqa: E731
    sheets.append({"name": "Teams", "columns": ["Site", "Directorate", "Team", "Manager", "Incidents", "Open now", "Open at the start", "Over old days", "Median hours",
                                                "SLA met %", "Bare notes %", "Change tasks closed", "Change tasks open", "Top issue"],
                   "rows": [[place(t["team"], "site"), place(t["team"], "dir"), t["team"], lead_of(t["team"]), t["incidents"],
                             t["backlog"][0], t["backlog"][1], t["old"], t["median_h"], t["sla"], t["quality"][2], t.get("ct_closed"), t.get("ct_open"), t["top"][0]]
                            for t in s.get("teams") or []]})
    sc = model.get("scopes") or {}
    lv = [(k, v) for k, v in sc.items() if v.get("level") in ("site", "dir")]
    if lv:
        sheets.append({"name": "Sites and directorates", "columns": ["Site", "Directorate", "Incidents", "Requests", "Open now", "Open at the start",
                                                                      "Over old days", "Median hours", "SLA met %", "Bare notes %",
                                                                      "Change tasks closed", "Change tasks open", "Top issue"],
                       "rows": [[v["site"], v.get("dir") or "", v["counts"]["incidents"], v["counts"]["requests"], v["backlog"][0], v["backlog"][1],
                                 v["age"][3], v["median_h"][0], v["sla"][0], v["quality"][2], (v.get("ctasks") or {}).get("closed"),
                                 (v.get("ctasks") or {}).get("open"), v["issues"][0]["name"] if v["issues"] else ""]
                                for k, v in sorted(lv, key=lambda kv: (_level_key(kv[1]["site"]), kv[1]["level"] != "site", _level_key(kv[1].get("dir") or "")))]})
    if (model.get("vip") or {}).get("on"):
        # 1.43.0: the VIP tickets, counts only - every scope (All, sites or units, teams) by tier; never a ticket's tier
        path = lambda v: [c_[1] for c_ in v.get("crumbs") or [["", v["name"]]]]          # noqa: E731  All, then each unit above its teams
        vs = sorted([(k, v) for k, v in sc.items() if v.get("vip")], key=lambda kv: [_nat(x) for x in path(kv[1])])
        sheets.append({"name": "VIP", "columns": ["Scope", "Level", "Tier", "Incidents", "Requests", "Open now", "No SLA"],
                       "rows": [[" > ".join(path(v)), v.get("level") or "", r_["label"], r_["incidents"],
                                 r_["requests"], r_["open"], r_["no_slo"]] for k, v in vs for r_ in v["vip"]["rows"]]})
    sheets.append({"name": "Hand-off paths", "columns": ["Path", "Incidents", "Most common issue", "Days added", "Examples"],
                   "rows": [[" > ".join(e["path"]), e["count"], e["issue"], e["added_days"], ", ".join(e["items"][:5])] for e in s["handoffs"]["paths"]]})
    # 1.9.0
    lc, hs, wl = s.get("lifecycle") or {}, s.get("hotspots") or {}, s.get("workload") or {}
    if lc.get("measured"):
        sheets.append({"name": "Time in state", "columns": ["Group", "Incidents", "Waiting to start (h)", "In progress (h)", "On hold (h)", "Resolved to closed (h)"],
                       "rows": [[g["group"], g["n"]] + [g[k_] for k_ in STATE_KINDS] for g in lc.get("by_group") or []]})
    sheets.append({"name": "Hot CIs", "columns": ["Configuration item", "Incidents", "Back within %d days" % (hs.get("repeat_days") or 7), "Linked to a problem", "Mostly in"],
                   "rows": [[c_["ci"], c_["count"], c_["repeats"], c_["problem"], c_["group"]] for c_ in hs.get("cis") or []]})
    if wl.get("person_metrics"):
        sheets.append({"name": "Workload", "columns": ["Person", "Group", "Open now", "Resolved", "Median hours to resolve"],
                       "rows": [[x["person"], x["group"], x["open"], x["resolved"], x["median_h"]] for x in wl.get("rows") or []]})
    return sheets + breach_xlsx(s.get("breach"))                # 1.59.0


def servicenow_insights_artifact(model):
    """The job artifact servicenow_insights: the window and each scope's main numbers (not the records)."""
    out = {"window": model.get("window"), "act": model.get("act"), "mask": model.get("mask"), "scopes": {}}
    for k, s in (model.get("scopes") or {}).items():
        out["scopes"][k] = {"counts": s["counts"], "backlog": s["backlog"], "median_h": s["median_h"], "sla": s["sla"],
                            "quality": s["quality"], "issues": [[i["name"], i["count"]] for i in s["issues"][:10]],
                            "requests": [[q["name"], q["count"]] for q in s["requests"][:10]], "summary": s["summary"],
                            "change_tasks": dict((k, v) for k, v in (s.get("ctasks") or {}).items() if k != "age") or None}
    return out


TIMING = (("ServiceNow reads", (("start", "read"),)), ("ACT", (("read", "act_a"), ("model", "act_b"))),
          ("The report worked out", (("act_a", "model"), ("act_b", "finish"))), ("The report database (read and written)", (("finish", "db"),)),
          ("Pages and email", (("db", "pages"),)))


def servicenow_insights_timing(t):
    """The run's time by phase (1.14.0), from the role's checkpoints (_sni_t: {name: epoch seconds}): {'rows': [[phase,
    seconds]] - the phases done so far -, 'total', 'line' (the job output's)}."""
    t = dict((k, float(v)) for k, v in (t or {}).items() if v is not None)
    rows = []
    for label, spans in TIMING:
        got = [t[b] - t[a] for a, b in spans if a in t and b in t]
        if got:
            rows.append([label, round(max(0.0, sum(got)), 1)])
    last = max(t.values()) if t else 0.0
    total = round(last - t["start"], 1) if "start" in t else 0.0

    def fmt(x):
        return ("%.0f s" % x) if x < 90 else ("%.1f min" % (x / 60.0))
    line = "Time: %s - %s in all" % (", ".join("%s %s" % (lbl, fmt(x)) for lbl, x in rows), fmt(total)) if rows else "Time: not measured"
    return {"rows": rows, "total": total, "line": line}


def rel_link(target, page):
    """The address of the file target (inside the report folder) as a link from the page file (both relative to the
    folder: 'ticket-insights/ops-windows/latest.html' from 'ticket-insights/2026-11-01_0605_job-7.html' ->
    'ops-windows/latest.html')."""
    import posixpath
    if not target:
        return ""
    return posixpath.relpath(str(target), posixpath.dirname(str(page or "")) or ".")


def web_links(web, page, variant=""):
    """The links of one page to the others (the menu, the side bar, side by side, the breadcrumbs): {scope key: address,
    'all': address} - a team by its name, a site 'site:NAME', a directorate 'dir:SITE/NAME' (0.97.0) - each to the newest
    page of that scope (its latest, else this run's page), relative to page. web: the role's _sni_web."""
    w = web or {}
    out = {}
    src = (w.get("variants") or {}).get(variant) if variant else None      # a split's half: its own pages (0.97.0)
    for key, f in list((w.get("pages") or w.get("teams") or {}).items()) + list((src or {}).items()):   # a team without one: its page
        if key != "all":
            out[key] = rel_link(f.get("latest") or f.get("page"), page)
    a = (src or {}).get("all") or w.get("all") or {}
    out["all"] = rel_link(a.get("latest") or a.get("page"), page)
    return out


def variant_links(web, key, page):
    """The split's switch on one page (0.97.0): {'': the page for every group, 'in': ..., 'out': ...} relative to page;
    {} without a split."""
    w = web or {}
    if not w.get("variants"):
        return {}
    base = w.get("all") or {} if key == "all" else (w.get("pages") or {}).get(key) or {}
    if not all(key in files for files in w["variants"].values()):          # no split pages for it (split_teams false)
        return {}
    out = {"": rel_link(base.get("latest") or base.get("page"), page)}
    for v, files in w["variants"].items():
        out[v] = rel_link((files.get(key) or {}).get("page"), page)
    return out


def servicenow_insights_variant(model, variant=""):
    """The model with a split's half's figures (0.97.0): the email's report of a variant page."""
    m = model or {}
    return dict(m, scopes=m["variants"][variant]) if variant and (m.get("variants") or {}).get(variant) else m


class FilterModule(object):
    def filters(self):
        return {"servicenow_insights_mask": servicenow_insights_mask, "servicenow_insights_descriptions": servicenow_insights_descriptions,
                "servicenow_insights_act_cache_key": servicenow_insights_act_cache_key, "servicenow_insights_act_cached": servicenow_insights_act_cached,
                "servicenow_insights_act_cache_rows": servicenow_insights_act_cache_rows,
                "servicenow_insights_batches": servicenow_insights_batches, "servicenow_insights_state": servicenow_insights_state,
                "servicenow_insights_lead_mail": servicenow_insights_lead_mail,
                "servicenow_insights_classify_evidence": servicenow_insights_classify_evidence,
                "servicenow_insights_classify_task": servicenow_insights_classify_task,
                "servicenow_insights_classify_merge": servicenow_insights_classify_merge,
                "servicenow_insights_fix_batches": servicenow_insights_fix_batches,
            "servicenow_insights_fix_ids": servicenow_insights_fix_ids,
            "servicenow_insights_fix_work_notes": servicenow_insights_fix_work_notes,
            "servicenow_insights_work_notes_state": servicenow_insights_work_notes_state,
            "servicenow_insights_mask_more": servicenow_insights_mask_more, "servicenow_insights_cut_lines": cut_lines, "servicenow_insights_fix_evidence": servicenow_insights_fix_evidence,
                "servicenow_insights_fix_task": servicenow_insights_fix_task, "servicenow_insights_fix_merge": servicenow_insights_fix_merge,
                "servicenow_insights_teams": servicenow_insights_teams, "servicenow_insights_model": servicenow_insights_model,
                "servicenow_insights_prefix_errors": servicenow_insights_prefix_errors,
                "servicenow_insights_vip_errors": servicenow_insights_vip_errors,
                # 1.59.0: about to breach
                "servicenow_insights_breach_horizon": servicenow_insights_breach_horizon,
                "servicenow_insights_breach_only": servicenow_insights_breach_only,
                "servicenow_insights_breach_doc": servicenow_insights_breach_doc,
                "servicenow_insights_breach_xlsx": servicenow_insights_breach_xlsx,
                "servicenow_insights_summary_evidence": servicenow_insights_summary_evidence,
                "servicenow_insights_summary_task": servicenow_insights_summary_task,
                "servicenow_insights_summary_merge": servicenow_insights_summary_merge, "servicenow_insights_finish": servicenow_insights_finish,
                "servicenow_insights_doc": servicenow_insights_doc, "servicenow_insights_xlsx": servicenow_insights_xlsx,
                "servicenow_insights_artifact": servicenow_insights_artifact, "servicenow_insights_timing": servicenow_insights_timing, "servicenow_insights_name_list": name_list,
                "servicenow_insights_slug": _slug, "servicenow_insights_not_read": not_read_lines,
                "servicenow_insights_rel": rel_link, "servicenow_insights_links": web_links,
                "servicenow_insights_variant_links": variant_links, "servicenow_insights_variant": servicenow_insights_variant,
                "servicenow_insights_ids": lambda rows: [{"id": str(x.get("id"))} for x in rows or []]}
