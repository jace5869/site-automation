# -*- coding: utf-8 -*-
"""Ticket insights (roles/servicenow_insights, docs/SERVICENOW_INSIGHTS.md): what the report knows, worked out from the
records site_servicenow_insights read - the issues, keeping up, severity, problem candidates, hand-offs, requests,
changes, close notes, All-stars, knowledge drafts - for the whole site and for each team (the model), and the pieces
around it: the masking before ACT (GenAI) and the questions ACT is asked, in batches, with its answers read back; the
email's report (site_email's _report), the workbook and the job artifact. The web page is servicenow_insights_page.py."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
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
ID10 = re.compile(r"(?<![\w.-])\d{10}(?![\w.-])")
FQDN = re.compile(r"(?<![\w.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:mil|gov|com|net|org|edu|local|lan|internal|corp|us)(?![\w.-])", re.I)
HOSTLIKE = re.compile(r"(?<![\w.-])[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\d[A-Za-z0-9-]*(?![\w.-])")
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


def sla_target(it, o):
    """Your own SLA target for a ticket, in hours (servicenow_insights_sla_targets: by table, then priority, "*" for the
    rest; servicenow_insights_sla_items: a catalog item's own) - or None: ServiceNow's Made SLA counts then."""
    items = (o or {}).get("sla_items") or {}
    if it.get("table") == "sc_req_item" and it.get("cat_item") in items:
        return float(items[it["cat_item"]])
    by = ((o or {}).get("sla_targets") or {}).get(it.get("table")) or {}
    by = dict((str(k).strip().upper().lstrip("P"), v) for k, v in by.items())
    v = by.get(str(it.get("priority_value") or "").strip(), by.get("*"))
    return None if v in (None, "") else float(v)


def apply_sla_targets(items, o):
    """Made SLA from your targets where you set one: resolved (or closed) within the target's hours. A ticket not yet
    done keeps None (only done tickets count, as with ServiceNow's flag)."""
    for it in items:
        t = sla_target(it, o)
        if t is None:
            continue
        h = _hours(it)
        it["sn_made_sla"] = it.get("made_sla")
        it["made_sla"] = None if h is None else h <= t
        it["sla_target_h"] = t


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
    found = {}                 # real -> kind
    for p in _people(items):
        found.setdefault(p, "PERSON")
        parts = p.replace(",", " ").split()
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            if "," not in p:
                found.setdefault("%s, %s" % (last, first), "PERSON")
            for w in (last,):
                if len(w) >= 4 and w.lower() not in COMMON_WORDS and w[:1].isupper():
                    found.setdefault(w, "PERSON")
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
    return {"pairs": pairs, "counts": counts}


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
    return {"pairs": pairs, "counts": counts}


def _mask_parts(mask):
    """(pattern of every real value, real -> placeholder, placeholder -> real, words of the real values) - kept for the
    last few masks seen (never stored on the mask itself: it is a fact)."""
    pairs = (mask or {}).get("pairs") or []
    for hit in _MASK_CACHE:
        if hit[0] is pairs:
            return hit[1:]
    rx = re.compile(r"(?<![\w@.-])(?:%s)(?![\w@-]|\.\w)" % "|".join(re.escape(p[0]) for p in pairs), re.I) if pairs else None
    fwd = dict((p[0].lower(), p[1]) for p in pairs)
    rev = dict((p[1], p[0]) for p in pairs)
    words = set(w for p in pairs for w in _norm(p[0]).split())
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


def servicenow_insights_descriptions(r, mask=None, opts=None):
    """The incidents' descriptions ACT groups: one per key (the most frequent first, at most act_max_descriptions), with
    how many incidents have it and their most common category. -> [{id, key, text (masked), count, category}]."""
    o = opts or {}
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
    rows = sorted(agg.values(), key=lambda a: (-a["count"], a["key"]))[:int(_o(o, "act_max_descriptions", 2000))]
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


def servicenow_insights_classify_merge(state, raw, batch, opts=None, ran=True, reason="", model=""):
    """One batch's answer into the state: its new issues (up to act_max_issues; past that, the closest ... Other), each
    description's issue. A batch not answered leaves its descriptions to their category."""
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
        n = _clean(x.get("name"), 80)
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
        n = _clean(x.get("issue"), 80)
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
        '{"items": [{"id": "I1", "steps": ["...", "..."], "support": [12, 3], "source": "tickets", "why": "...", "disagree": false, '
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


def servicenow_insights_hierarchy(names, opts=None, tree=None, tree_ok=True):
    """Every group's place (hier_place; its ServiceNow parents from tree, the groups as the module read them) ->
    {group: {site, dir, team, rule, by, under}}."""
    o = opts or {}
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
    teams = list(fixed["teams"]) if fixed else servicenow_insights_teams(r, o)
    repeat = datetime.timedelta(days=float(_o(o, "repeat_days", 14)))
    old_days = float(_o(o, "old_days", 30))
    weeks = _weeks(since, until, tz)

    apply_sla_targets(items, o)
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
            k = (x.get("priority_value") or "9", x.get("priority") or "(none)")
            e = prio.setdefault(k, {"label": k[1], "value": k[0], "count": 0, "hours": [], "sla": [0, 0]})
            e["count"] += 1
        for x in res_now:
            k = (x.get("priority_value") or "9", x.get("priority") or "(none)")
            if k in prio:
                prio[k]["hours"].append(_hours(x))
                if x.get("made_sla") is not None:
                    prio[k]["sla"][1] += 1
                    prio[k]["sla"][0] += 1 if x["made_sla"] else 0
        prio_rows = [{"label": e["label"], "value": e["value"], "count": e["count"], "median_h": _r1(_median(e["hours"])),
                      "sla": _r1(_pct(e["sla"][0], e["sla"][1])),
                      "target_h": sla_target({"table": "incident", "priority_value": e["value"]}, o)} for _, e in sorted(prio.items())]
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
                               "weekly": wkly.get(n, []), "kind": d.get("kind") or "incident", "about": d.get("about") or ""})
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
                             "weekly": rw.get(n, [])})

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

        lvl = level or ("all" if allscope else "team")
        sc = {"key": key or ("all" if allscope else team), "name": name or ("All assignment groups" if allscope else team),
              "slug": "all" if allscope else _slug(name or team), "team": team, "level": lvl,
              "counts": {"incidents": len(inc_now), "incidents_prev": len(inc_prev), "requests": len(ritm_now),
                         "changes": len(chg), "problems": sum(1 for p in by_table.get("problem", []) if mine(p) and
                                                              (_in(_t(p.get("opened_at")), since, until) or p.get("active"))),
                         "resolved": len(res_now)},
              "median_h": [_r1(_median([_hours(x) for x in res_now])), _r1(_median([_hours(x) for x in res_prev]))],
              "sla": [_r1(_pct(sum(1 for x in sla_now if x["made_sla"]), len(sla_now))), _r1(_pct(sum(1 for x in sla_prev if x["made_sla"]), len(sla_prev)))],
              "reopen": [_r1(reo[0]), _r1(reo[1])], "came_back": [_r1(cb[0]), _r1(cb[1])],
              "quality": q_now, "quality_prev": q_prev, "notes_graded": q_n,
              "backlog": [bl_now, bl_start], "age": age, "weeks": wk, "heat": heat,
              "prio": prio_rows, "majors": major_rows, "issues": issue_rows, "issue_total": len(cur), "misfiled": misfiled[:5],
              "requests": req_rows, "changes": changes, "notes_by_group": q_groups, "bare": bare_rows,
              "handoffs": handoffs, "problems": pc[:15], "resolved_n": len(rep_now)}
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
        for x in inc_all + by_table.get("sc_task", []) + by_table.get("sc_req_item", []):
            if (x.get("group") or "") == team and (_in(_t(x.get("opened_at")), since, until) or _in(_t(x.get("done_at")), since, until)):
                for k in ("assigned_to", "resolved_by", "closed_by"):
                    if x.get(k):
                        names.add(x[k])
        return names, False

    def senders(team, roster):
        """Who opens the team's tickets (Opened by, on the tickets assigned to it): [{name, opened, from, member, end_user}]."""
        cnt, own = {}, {}
        for x in inc_all + by_table.get("sc_req_item", []):
            if (x.get("group") or "") != team or not _in(_t(x.get("opened_at")), since, until):
                continue
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
        return rows

    def stars(team):
        weight = float(_o(o, "bare_weight", 0.5))
        done = [x for x in inc_all + by_table.get("sc_task", []) if (x.get("group") or "") == team and _in(_t(x.get("done_at")), since, until)]
        roster, known = roster_of(team)
        people = dict((who, {"name": who, "resolved": 0, "score": 0.0, "hours": [], "notes": [], "reopen": 0, "came_back": 0, "inc": 0, "opened": 0})
                      for who in roster)
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
                         "quality": q, "repeat": _r1(_pct(rep, p["inc"])) if p["inc"] else None, "opened": p["opened"]})
        rows.sort(key=lambda p: (-p["score"], -p["resolved"], p["name"]))
        elig = [p for p in rows if p["resolved"] >= int(_o(o, "star_min", 25))]
        awards = []
        if rows and max(p["resolved"] for p in rows) > 0:
            m = max(rows, key=lambda p: (p["resolved"], p["name"]))
            awards.append({"icon": "trophy", "what": "Most resolved", "name": m["name"], "stat": "%d tickets resolved" % m["resolved"]})
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
        return {"people": rows[:25], "awards": awards,
                "total": {"resolved": tr + sum(outside.values()), "quality": team_q,
                          "repeat": _r1(sum(p["repeat"] for p in reps) / len(reps)) if reps else None},
                "members_known": known, "members": len(roster), "outside": {"count": sum(outside.values()), "names": _top(outside, 3)},
                "openers": senders(team, roster), "more": max(0, len(rows) - 25)}

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
    sites = sorted(site_g, key=_level_key)
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
                "series": [w["backlog"] for w in s["weeks"]], "old": s["age"][3], "median_h": s["median_h"][0], "sla": s["sla"][0],
                "quality": s["quality"], "top": [s["issues"][0]["name"], s["issues"][0]["count"]] if s["issues"] else ["", 0],
                "incidents": s["counts"]["incidents"]}
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
    scopes["all"]["teams"] = [side_row(t) for t in sorted(teams, key=_nat)]          # every team (the email's table, the workbook)
    ids = {"all": "all"}
    for i, n_ in enumerate(tree):
        ids[n_["key"]] = "S%d" % (i + 1)
    for i, k in enumerate([d["key"] for n_ in tree for d in n_["dirs"]]):
        ids[k] = "D%d" % (i + 1)
    for i, t in enumerate(teams):
        ids[t] = "T%d" % (i + 1)
    model = {"window": win, "url": r.get("url") or "", "teams": teams, "scopes": scopes, "reopen_used": reopen_used,
             "tree": tree, "pages": pages, "scope_ids": ids, "hier": hier,
             "hier_other": sorted([g for g, h in hier.items() if h["dir"] == OTHER], key=_nat),
             "hier_unknown": [[g, gcount.get(g, 0)] for g in sorted([g for g, h in hier.items() if h["rule"] == "unknown"], key=_nat)],
             "hier_folded": [[g, why, "%s / %s" % (hier[g]["site"], hier[g]["dir"])] for g, why in sorted(folded.items(), key=lambda kv: _nat(kv[0]))
                             if g in hier],
             "placed_by": dict((b, sum(1 for h in hier.values() if h["by"] == b)) for b in sorted(set(h["by"] for h in hier.values()))),
             "group_tree_error": str(r.get("group_tree_error") or ""),
             "reopened_n": reopened, "incidents_n": len(inc_win), "short": short_names(group_names),
             "members_read": members_known, "members_error": str(r.get("members_error") or ""),
             "tables": r.get("tables") or [], "groups": r.get("groups") or [], "audit_error": r.get("audit_error") or "",
             "audit_read": bool(audit) or not r.get("audit_error"), "lookup_note": r.get("lookup_note") or "",
             "audit_source": r.get("audit_source") or ("sys_audit" if audit else ""), "audit_note": r.get("audit_note") or "",
             "act": {"on": bool(o.get("act")), "ran": act_ran, "asked": int(state.get("asked") or 0), "answered": int(state.get("answered") or 0),
                     "errors": list(state.get("errors") or [])[:20], "model": state.get("model") or "", "provider": state.get("provider") or "",
                     "issues": len(state.get("issues") or []), "fixes": len(state.get("fixes") or {}), "reason": str(o.get("act_reason") or ""),
                     "work_notes": dict(state.get("work_notes") or {})},
             "mask": dict(mask.get("counts") or {}), "summaries_by": "rules"}
    for k, s in scopes.items():
        s["summary"] = rule_summary(s, model, o)
    # the split (0.97.0): every page's figures again for the groups that match servicenow_insights_split_pattern (the group
    # or a parent team below the root) and for the rest - worked out here, so the page only switches between them
    label, pat = str(o.get("split_label") or "").strip(), str(o.get("split_pattern") or "").strip().lower()
    if label and pat and not fixed:
        def is_split(g):
            below = (group_chain(g, gtree) if tree_ok else [])[:-1]
            return fnmatch.fnmatchcase(g.lower(), pat) or any(fnmatch.fnmatchcase(a.lower(), pat) for a in below)
        sgroups = sorted([g for g in hier if is_split(g)], key=_nat)
        model["split"] = {"label": label, "groups": sgroups}
        model["variants"] = {}
        for v in ("in", "out"):
            rv = dict(r, items=[it for it in r.get("items") or [] if (it.get("group") in sgroups) == (v == "in")])
            mv = servicenow_insights_model(rv, state, mask, dict(o, split_pattern="", _fixed={"teams": teams, "hier": hier, "folded": folded}))
            model["variants"][v] = mv["scopes"]
    return model


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
        out.append("The backlog went from %d to %d (%s); %d %s older than %s days." % (
            b[1], b[0], _change(b[0], b[1]) or "no change", s["age"][3], "is" if s["age"][3] == 1 else "are", int(_o(opts, "old_days", 30))))
    if s["majors"]:
        chg = sum(1 for m in s["majors"] if m["change"])
        out.append("%d P1/P2 incident%s%s." % (len(s["majors"]), "" if len(s["majors"]) == 1 else "s",
                                               (", %d of them caused by a change" % chg) if chg else ""))
    if s["requests"]:
        out.append("The most requested service is %s (%d)." % (s["requests"][0]["name"], s["requests"][0]["count"]))
    if s.get("notes_graded"):
        out.append("%d%% of close notes say what was done; %d%% are a stock phrase such as \"fixed\" or \"done\", or empty." % (
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
               {"label": "SLAs met", "value": ("%.1f%%" % s["sla"][0]) if s["sla"][0] is not None else "—"},
               {"label": "Close notes that say what was done", "value": "%d%%" % s["quality"][0]}]
    secs = [{"title": "What the period says" + (" (written by ACT)" if s.get("summary_by") == "act" else ""), "lines": s["summary"] or ["(nothing to say)"]}]
    side = (s.get("side") or {}).get("rows") or (s.get("teams") if key in (None, "all") else None)
    if side:
        kind = (s.get("side") or {}).get("kind") or "Teams"
        secs.append({"title": "%s side by side" % kind, "columns": [kind[:-1] if kind != "Directorates" else "Directorate", "Incidents", "Open now", "Was", "Over %d days" % int(_o(o, "old_days", 30)),
                                                               "Median fix", "SLA met", "Bare notes", "Top issue"],
                     "rows": [[t.get("label") or t["team"], t["incidents"], t["backlog"][0], t["backlog"][1], t["old"], fmt_hours(t["median_h"]),
                               ("%.1f%%" % t["sla"]) if t["sla"] is not None else "—", "%d%%" % t["quality"][2],
                               ("%s (%d)" % (t["top"][0], t["top"][1])) if t["top"][0] else ""] for t in side],
                     "row_status": ["warning" if t["backlog"][0] > t["backlog"][1] * 1.05 and t["backlog"][0] - t["backlog"][1] >= 5 else ""
                                    for t in side]})
    src = {"tix": "from the tickets", "mix": "partly from tickets", "ai": "suggested by ACT", "none": "no fix in the notes"}
    n = int(_o(o, "top_issues", 10))
    if s["issues"]:
        secs.append({"title": "Top incident issues", "columns": ["#", "Rank", "Issue", "Incidents", "Was", "Median fix", "Bare notes", "Fix"],
                     "rows": [[i["rank"], i["mv"]["text"], i["name"], i["count"], i["prev"], fmt_hours(i["median_h"]), "%d%%" % i["quality"][2],
                               src.get(i["fix"]["source"], "")] for i in s["issues"][:n]]})
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
                                (" - resolved, incidents continue" if p["continues"] else "")) if p["problem"] else "none"] for p in s["problems"][:10]],
                     "row_status": ["warning" if not p["problem"] or p["continues"] else "" for p in s["problems"][:10]]})
    if s["changes"]["rows"]:
        secs.append({"title": "Changes failed or backed out", "columns": ["Change", "Closed", "What", "Group", "Outcome", "Incidents that followed"],
                     "rows": [[x["number"], x["closed"], x["what"], x["group"], x["outcome"],
                               ", ".join("%s (%s)" % (f[0], "caused by" if f[1] == "link" else "same CI") for f in x["followed"]) or "none"]
                              for x in s["changes"]["rows"][:20]]})
    nr = not_read_lines(model)
    if nr:
        secs.append({"title": "Not read", "status": "warning", "lines": nr})
    secs.append({"title": "Read", "lines": ["%s: %d record(s) kept of %d read" % (t["table"], t["kept"], t["read"]) for t in model.get("tables") or [] if not t.get("error")] +
                 ["Window: %s, compared with %s (%s)" % (win.get("label"), win.get("prev_label") or "nothing", win.get("time_zone") or "UTC")] +
                 ["ACT: %s" % act_line(model)]})
    return {"title": title, "subtitle": "%s - %s" % (s["name"], win.get("label") or ""), "status": status, "summary": summary, "sections": secs,
            "footer": "ServiceNow %s" % (model.get("url") or "")}


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


def not_read_lines(model):
    out = []
    for t in model.get("tables") or []:
        if t.get("error"):
            out.append("%s: %s" % (t["table"], t["error"]))
        elif t.get("more"):
            out.append("%s: more than the first %d records - raise servicenow_insights_max_records, or shorten the window" % (t["table"], t["read"]))
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
    if model.get("group_tree_error"):
        out.append("sys_user_group (the groups' parents, for the sites and directorates): %s - placed by their names instead"
                   % model["group_tree_error"])
    if ((model.get("act") or {}).get("work_notes") or {}).get("error"):
        out.append("sys_journal_field (the incidents' work notes, for ACT's fixes): %s - the fixes were written from the close notes "
                   "only" % model["act"]["work_notes"]["error"])
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
    apply_sla_targets(inc, o)
    sheets = [{"name": "Incidents", "columns": ["Number", "Short description", "Issue", "Category", "Subcategory", "Priority", "State",
                                                "Site", "Directorate", "Placed by", split_col, "Assignment group", "Assigned to", "Resolved by", "Caller", "Configuration item", "Opened",
                                                "Resolved", "Hours to resolve", "Reassignments", "Reopened", "Made SLA", "SLA target (hours)", "Close code",
                                                "Close note grade", "Close notes", "Caused by", "Problem"],
               "rows": [[x.get("number"), x.get("short_description"), issues_of.get(x["sys_id"]) or category_issue(x), x.get("category"),
                         x.get("subcategory"), x.get("priority"), x.get("state"), place(x.get("group"), "site"), place(x.get("group"), "dir"), place(x.get("group"), "by"),
                         ("yes" if x.get("group") in split_groups else "no") if split_groups is not None else "",
                         x.get("group"), x.get("assigned_to"), x.get("resolved_by"),
                         x.get("caller"), x.get("cmdb_ci"), when(x.get("opened_at")), when(x.get("done_at")), _r1(_hours(x)),
                         x.get("reassignment_count"), x.get("reopen_count"), "" if x.get("made_sla") is None else ("yes" if x["made_sla"] else "no"),
                         x.get("sla_target_h", ""), x.get("close_code"), grade(x.get("close_notes"), o) if x.get("done_at") else "", x.get("close_notes"),
                         x.get("caused_by"), x.get("problem")] for x in inc]}]
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
    sheets.append({"name": "Changes failed", "columns": ["Change", "Closed", "What", "Group", "Outcome", "Followed by"],
                   "rows": [[x["number"], x["closed"], x["what"], x["group"], x["outcome"], ", ".join(f[0] + (" (caused by)" if f[1] == "link" else " (same CI)") for f in x["followed"])]
                            for x in s["changes"]["rows"]]})
    sheets.append({"name": "Problem candidates", "columns": ["Issue", "CI", "Last days", "In period", "Group", "Problem", "State", "Incidents continue"],
                   "rows": [[p["issue"], p["ci"], p["last"], p["count"], p["group"], p["problem"], p["problem_state"], "yes" if p["continues"] else ""]
                            for p in s["problems"]]})
    people = []
    for t in model.get("teams") or []:
        st = model["scopes"][t].get("stars") or {}
        for p in st.get("people") or []:
            people.append([t, p["name"], "member" if st.get("members_known") else "worked its tickets", p["resolved"], p["score"], p["median_h"],
                           p["quality"][0], p["quality"][2], p["repeat"], p["opened"]])
        if (st.get("outside") or {}).get("count"):
            people.append([t, "(resolved by people outside the team: %s)" % ", ".join(st["outside"]["names"]), "outside", st["outside"]["count"],
                           "", "", "", "", "", ""])
    sheets.append({"name": "People", "columns": ["Team", "Person", "In the team", "Resolved", "Score", "Median hours", "Good notes %", "Bare notes %",
                                                 "Reopened %" if model.get("reopen_used") else "Came back %", "Opened"], "rows": people})
    sheets.append({"name": "Teams", "columns": ["Site", "Directorate", "Team", "Incidents", "Open now", "Open at the start", "Over old days", "Median hours", "SLA met %",
                                                "Bare notes %", "Top issue"],
                   "rows": [[place(t["team"], "site"), place(t["team"], "dir"), t["team"], t["incidents"], t["backlog"][0], t["backlog"][1], t["old"],
                             t["median_h"], t["sla"], t["quality"][2], t["top"][0]] for t in s.get("teams") or []]})
    sc = model.get("scopes") or {}
    lv = [(k, v) for k, v in sc.items() if v.get("level") in ("site", "dir")]
    if lv:
        sheets.append({"name": "Sites and directorates", "columns": ["Site", "Directorate", "Incidents", "Requests", "Open now", "Open at the start",
                                                                      "Over old days", "Median hours", "SLA met %", "Bare notes %", "Top issue"],
                       "rows": [[v["site"], v.get("dir") or "", v["counts"]["incidents"], v["counts"]["requests"], v["backlog"][0], v["backlog"][1],
                                 v["age"][3], v["median_h"][0], v["sla"][0], v["quality"][2], v["issues"][0]["name"] if v["issues"] else ""]
                                for k, v in sorted(lv, key=lambda kv: (_level_key(kv[1]["site"]), kv[1]["level"] != "site", _level_key(kv[1].get("dir") or "")))]})
    sheets.append({"name": "Hand-off paths", "columns": ["Path", "Incidents", "Most common issue", "Days added", "Examples"],
                   "rows": [[" > ".join(e["path"]), e["count"], e["issue"], e["added_days"], ", ".join(e["items"][:5])] for e in s["handoffs"]["paths"]]})
    return sheets


def servicenow_insights_artifact(model):
    """The job artifact servicenow_insights: the window and each scope's main numbers (not the records)."""
    out = {"window": model.get("window"), "act": model.get("act"), "mask": model.get("mask"), "scopes": {}}
    for k, s in (model.get("scopes") or {}).items():
        out["scopes"][k] = {"counts": s["counts"], "backlog": s["backlog"], "median_h": s["median_h"], "sla": s["sla"],
                            "quality": s["quality"], "issues": [[i["name"], i["count"]] for i in s["issues"][:10]],
                            "requests": [[q["name"], q["count"]] for q in s["requests"][:10]], "summary": s["summary"]}
    return out


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
                "servicenow_insights_batches": servicenow_insights_batches, "servicenow_insights_state": servicenow_insights_state,
                "servicenow_insights_classify_evidence": servicenow_insights_classify_evidence,
                "servicenow_insights_classify_task": servicenow_insights_classify_task,
                "servicenow_insights_classify_merge": servicenow_insights_classify_merge,
                "servicenow_insights_fix_batches": servicenow_insights_fix_batches,
            "servicenow_insights_fix_ids": servicenow_insights_fix_ids,
            "servicenow_insights_fix_work_notes": servicenow_insights_fix_work_notes,
            "servicenow_insights_work_notes_state": servicenow_insights_work_notes_state,
            "servicenow_insights_mask_more": servicenow_insights_mask_more, "servicenow_insights_fix_evidence": servicenow_insights_fix_evidence,
                "servicenow_insights_fix_task": servicenow_insights_fix_task, "servicenow_insights_fix_merge": servicenow_insights_fix_merge,
                "servicenow_insights_teams": servicenow_insights_teams, "servicenow_insights_model": servicenow_insights_model,
                "servicenow_insights_summary_evidence": servicenow_insights_summary_evidence,
                "servicenow_insights_summary_task": servicenow_insights_summary_task,
                "servicenow_insights_summary_merge": servicenow_insights_summary_merge, "servicenow_insights_finish": servicenow_insights_finish,
                "servicenow_insights_doc": servicenow_insights_doc, "servicenow_insights_xlsx": servicenow_insights_xlsx,
                "servicenow_insights_artifact": servicenow_insights_artifact, "servicenow_insights_name_list": name_list,
                "servicenow_insights_slug": _slug, "servicenow_insights_not_read": not_read_lines,
                "servicenow_insights_rel": rel_link, "servicenow_insights_links": web_links,
                "servicenow_insights_variant_links": variant_links, "servicenow_insights_variant": servicenow_insights_variant,
                "servicenow_insights_ids": lambda rows: [{"id": str(x.get("id"))} for x in rows or []]}
