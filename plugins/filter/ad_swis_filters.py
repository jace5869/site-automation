# -*- coding: utf-8 -*-
"""1.94.0: the AD health report's "From SolarWinds" section (roles/ad_report/tasks/swis.yml): what site_ad_swis read
(plugins/module_utils/site_ad_swis.py) as a section after "Domain controllers' health", a tile, and the "Not read" lines.

ad_swis_view(res, ad, opts) -> {section | None, tile | None, not_read: [...], lines: [...]}
ad_swis_insert(report, view) -> the report with them in it (the email, the website and the workbook: the same report)

The section never changes the report's status or the job's result: SolarWinds' findings are said, AD's are counted."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import os
import re

_tm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py")
if os.path.isfile(_tm_path):
    _spec = importlib.util.spec_from_file_location("site_time", _tm_path)
    _tm = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_tm)
else:                                                    # 1.101.2: the portal's copy - site_time.py beside it
    import site_time as _tm

TITLE = "From SolarWinds"
AFTER = ("Domain controllers' health", "Domain controllers")
WORD = {"Down": "critical", "Critical": "critical", "Warning": "warning", "Unknown": "warning", "Up": "ok"}
SEV = {2: "critical", 3: "critical", 1: "warning"}        # Orion's alert severities: 2 critical, 3 serious, 1 warning
RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3, "": 4}


def _utc(text):
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)", str(text or ""))
    return datetime.datetime(*[int(g) for g in m.groups()]) if m else None


def _ago(minutes):
    if minutes is None:
        return ""
    m = int(minutes)
    return "%d min ago" % m if m < 120 else ("%d h ago" % (m // 60) if m < 2880 else "%d days ago" % (m // 1440))


def _dur(minutes):
    m = int(minutes or 0)
    return "%d min" % m if m < 120 else ("%.1f h" % (m / 60.0) if m < 2880 else "%d days" % (m // 1440))


def _ad_problems(ad):
    """_ad.problems by DC (its whole name, lower case: dc01 of two domains are two DCs) -> [(severity, text)], worst first."""
    out = {}
    for p in (ad or {}).get("problems") or []:
        k = str(p.get("object") or "").strip().lower()
        if k:                                            # (domain, DC): two domains' DC01 are two DCs
            out.setdefault((str(p.get("domain") or "").strip().lower(), k), []).append((str(p.get("severity") or ""), str(p.get("text") or "")))
    for v in out.values():
        v.sort(key=lambda x: RANK.get(x[0], 3))
    return out


def _probs_of(probs, d):
    """A DC's AD problems: by its domain and name; a DC whose domain is not known, by its name only when one domain has it."""
    dom, name = str(d.get("domain") or "").strip().lower(), str(d.get("dc") or "").strip().lower()
    if dom:
        return probs.get((dom, name), [])
    hits = [k for k in probs if k[1] == name]
    return probs[hits[0]] if len(hits) == 1 else []


def _meaning(d, probs, cpu_warn, mem_warn, now):
    """One short line: SolarWinds and AD read together."""
    adp = [t for s, t in probs if s == "critical"]
    ad_bad = ("AD says: %s" % adp[0][:70]) if adp else ""
    if not d.get("matched"):
        if d.get("unmatched_why"):
            return "Not matched: %s" % d["unmatched_why"]
        return "Not found by name%s: add it to SolarWinds' polling" % ((" or by its IP " + d["ip"]) if d.get("ip") else "")
    out = []
    if d.get("status") == "Down":
        out.append(("Down in SolarWinds and in AD (%s): the DC itself" % adp[0][:60]) if adp else
                   "Down in SolarWinds, but AD answered: check SolarWinds' polling of it")
    apps = d.get("apps") or {}
    if apps.get("down") and d.get("status") != "Down":
        out.append("AD monitors down: %s" % ", ".join([c.get("name") or "" for c in apps.get("not_up") or [] if c.get("status") == "Down"][:3]))
        if ad_bad:
            out.append(ad_bad)
    elif apps.get("warning") and d.get("status") != "Down":
        out.append("AD monitors warning: %s" % ", ".join([c.get("name") or "" for c in apps.get("not_up") or [] if c.get("status") == "Warning"][:2]))
    busy = [w for w, v, lim in (("CPU", _num(d.get("cpu")), cpu_warn), ("memory", _num(d.get("mem")), mem_warn)) if v is not None and v >= lim]
    if busy:
        out.append("Busy: %s near full" % " and ".join(busy))
    ld = d.get("last_down")
    if ld and not ld.get("ongoing") and d.get("status") != "Down":
        at = _utc(ld.get("at"))
        if at and (now - at).total_seconds() < 86400 * 2:
            out.append("back after an outage of %s" % _dur(ld.get("minutes")))
    if not out and d.get("alerts"):
        out.append("%d active alert%s" % (len(d["alerts"]), "" if len(d["alerts"]) == 1 else "s"))
    return "; ".join(out[:2]) or "-"


def ad_swis_view(res, ad, opts=None):
    """Never raises: a read the view cannot make into a section is one Not read line (the job goes on)."""
    try:
        return _view(res, ad, opts)
    except Exception as e:  # noqa: BLE001
        return {"section": None, "tile": None, "flat": None, "lines": ["From SolarWinds: not shown (%s)" % e.__class__.__name__],
                "not_read": ["SolarWinds: read, but not shown - the report could not make a table of it (%s)" % e.__class__.__name__]}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v


def _plain(v):
    """A tile's value that is a plain number - 3, 2.5, "1,200" - as report_headline's _plain_number reads it."""
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    return re.match(r"^-?\d+(\.\d+)?$", str(v).strip().replace(",", "")) is not None


def _view(res, ad, opts=None):
    o = opts or {}
    r = res or {}
    tz, _note = _tm.zone(o.get("time_zone"))
    cpu_warn, mem_warn, days = float(o.get("cpu_warn", 90)), float(o.get("mem_warn", 90)), int(o.get("days", 30))
    now = _utc(o.get("now")) or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    nr = []
    crashed = not r.get("asked") and not r.get("skipped") and any(r.get(k) for k in ("failed", "exception", "module_stderr", "msg"))
    if crashed:                                          # the module stopped (failed_when: false kept the job going)
        return {"section": None, "tile": None, "flat": None, "lines": ["From SolarWinds: the reader failed"],
                "not_read": ["SolarWinds: not read - the reader stopped with an error (the job's output has it; the report is complete apart from this)"]}
    if not r.get("asked"):
        if o.get("explain_off"):
            nr.append("SolarWinds: not read - %s" % o["explain_off"])
        return {"section": None, "tile": None, "flat": None, "not_read": nr, "lines": ["From SolarWinds: not read (no credential)"]}
    for w in r.get("why") or []:
        nr.append("SolarWinds: %s" % w)
    if not r.get("ok") or not r.get("dcs"):
        nr.append("SolarWinds: the domain controllers were not read (the report is complete apart from this)")
        return {"section": None, "tile": None, "flat": None, "not_read": nr, "lines": ["From SolarWinds: not read"]}
    probs = _ad_problems(ad)
    sam, events = bool(r.get("sam")), bool(r.get("events"))
    cols = ["", "DC", "SolarWinds"] + (["AD monitors (SAM)"] if sam else []) + ["CPU / memory (%)", "Alerts", "Last down (%d days)" % days, "What it means"]
    kinds = ["x", "", ""] + (["wrap"] if sam else []) + ["", "wrap", "wrap", "long"]
    rows = []
    for d in r["dcs"]:
        cells, stc = [], {}
        st = d.get("status") or ""
        found = bool(d.get("matched"))
        sw_cell = st if found else ("Not matched" if d.get("unmatched_why") else "Not in SolarWinds")
        stc["SolarWinds"] = WORD.get(st, "") if found else ("warning" if d.get("unmatched_why") else "info")
        apps = d.get("apps") or {}
        if sam:
            stc["AD monitors (SAM)"] = {"Down": "critical", "Warning": "warning", "Unknown": "warning" if st != "Down" else "critical"}.get(apps.get("status"), "")
        cpu, mem = _num(d.get("cpu")), _num(d.get("mem"))
        cm = "-" if cpu is None and mem is None else "%s / %s" % ("-" if cpu is None else int(round(float(cpu))), "-" if mem is None else int(round(float(mem))))
        if (cpu is not None and float(cpu) >= cpu_warn) or (mem is not None and float(mem) >= mem_warn):
            stc["CPU / memory (%)"] = "warning"
        al = d.get("alerts") or []
        al_cell = ("%d: %s" % (len(al), al[0].get("name") or al[0].get("message") or "alert")) if al else "-"
        if al:
            stc["Alerts"] = min((SEV.get(_int(a.get("severity")), "info") for a in al), key=lambda w: RANK.get(w, 3))
        ld = d.get("last_down")
        if not found:
            ld_cell = "-"
        elif not events:
            ld_cell = "not read live"
        elif not ld:
            ld_cell = "-"
        elif ld.get("ongoing"):
            ld_cell, stc["Last down (%d days)" % days] = "now, for %s" % _dur(ld.get("minutes")), "critical"
        elif ld.get("open"):                                 # a Node Down with no Node Up after it, but the node is up now
            ld_cell = "%s (its end not in SolarWinds' events)" % (_tm.local(ld.get("at") + " UTC", tz) if tz else ld.get("at") + " UTC")
        else:
            ld_cell = "%s, for %s" % (_tm.local(ld.get("at") + " UTC", tz) if tz else ld.get("at") + " UTC", _dur(ld.get("minutes")))
            if (now - (_utc(ld.get("at")) or now)).total_seconds() < 86400:
                stc["Last down (%d days)" % days] = "warning"
        row = {"": "", "DC": d["dc"], "SolarWinds": sw_cell, "AD monitors (SAM)": apps.get("text") or ("-" if not found else "no AD template"),
               "CPU / memory (%)": cm if found else "-", "Alerts": al_cell if found else "-", "Last down (%d days)" % days: ld_cell,
               "What it means": _meaning(d, _probs_of(probs, d), cpu_warn, mem_warn, now)}
        cs = [stc.get(c, "") for c in cols]
        worst = min([w for w in cs if w in RANK and w not in ("ok", "")] or [""], key=lambda w: RANK.get(w, 4))
        det = []
        many = len(set(c.get("app") for c in apps.get("not_up") or [])) > 1        # one template: its name is not repeated
        if sam and apps.get("not_up"):
            det.append({"title": "AD monitors (SAM) not up", "columns": ["Component", "Status", "Value", "Message"], "kinds": ["wrap", "nw", "nw", "wrap"],
                        "rows": [[(c.get("name") or "") + ((" (%s)" % c["app"]) if many and c.get("app") and c.get("app") != c.get("name") else ""),
                                  c.get("status") or "", "-" if c.get("value") in (None, "") else str(c.get("value")), c.get("message") or c.get("status_text") or "-"]
                                 for c in apps["not_up"][:20]]})
        if found:
            ifs = d.get("ifaces")
            nic = ("not in the snapshot (its reader comes with the SolarWinds snapshot)" if ifs is None else
                   ("not in the snapshot (it keeps the network links' interfaces; a DC's card is seldom one)" if r.get("source") == "db-snapshot"
                    else "not monitored") if not ifs else
                   "; ".join("%s: %s%s" % (i.get("name") or "?", i.get("status") or "?",
                                           (", %s errors and %s discards last hour" % (i.get("errors"), i.get("discards"))) if i.get("errors") is not None else "")
                             for i in ifs[:4]))
            lp = d.get("last_poll") or ""
            det.append({"title": "The node", "columns": ["Last poll", "Network card (only when SolarWinds monitors it)", "Matched by"],
                        "kinds": ["nw", "wrap", "nw"],
                        "rows": [[(_tm.local(lp + " UTC", tz) if tz else lp + " UTC") if lp else "-", nic, "its name" if d.get("matched") == "name" else "its IP"]]})
            if al:
                det.append({"title": "Active alerts", "columns": ["Alert", "Since", "Message"], "kinds": ["wrap", "nw", "wrap"],
                            "rows": [[a.get("name") or "-", _when(a.get("triggered_at"), tz), a.get("message") or "-"] for a in al[:10]]})
        rows.append((RANK.get(worst, 4), d["dc"].lower(), [row[c] for c in cols], cs, worst, det,
                     ("%s|%s" % (d.get("domain") or "", d["dc"])).lower()))
    rows.sort(key=lambda x: (x[0], x[1]))
    when = (_tm.local(r["read_at"] + " UTC", tz) if tz else r["read_at"] + " UTC") if r.get("read_at") else ""
    if r.get("source") == "db-snapshot":
        src = "from the SolarWinds snapshot of %s (%d min old%s)" % (when, r.get("age_min") or 0,
                                                                   (", taken every %d min" % r["interval_min"]) if r.get("interval_min") else "")
        if r.get("stale"):
            src += " - OLDER than ad_solarwinds_max_age (%d min): SolarWinds itself could not be read, so this is not today's state" % int(o.get("max_age", 60))
    else:
        src = "read live from SolarWinds at %s (%s)" % (when, "; ".join(r.get("why") or []) or "no report database with a snapshot")
    text = ("SolarWinds' view of each DC, read only, %s. Matched by name, then by IP.%s Last down: %s. Open a row for the "
            "components not up, the last poll and the network card." % (
                src, " AD monitors: the SolarWinds AD application templates (SAM)." if sam else (" " + (r.get("sam_why") or "No SAM on this Orion") + "."),
                "SolarWinds' node-down events" if events else "not read live (only the snapshot keeps SolarWinds' events)"))
    bad = [x for x in rows if x[4] in ("critical", "warning")]
    sec = {"title": TITLE, "counted": False, "status": "critical" if any(x[4] == "critical" for x in rows) else ("warning" if bad else "ok"),
           "columns": cols, "kinds": kinds, "rows": [x[2] for x in rows], "cell_status": [x[3] for x in rows],
           "row_status": [x[4] for x in rows], "details": [x[5] for x in rows], "text": text,
           "keys": [x[6] for x in rows]}                       # 1.101.2 review: each row's DC by domain and name (the portal's AD page)
    n_dn = sum(1 for x in rows if x[2][2] == "Down")
    tile = {"label": "SolarWinds: down or warning", "status": "critical" if any(x[4] == "critical" for x in rows) else ("warning" if bad else "ok"),
            "value": len(bad)}
    lines = ["From SolarWinds (%s): %d DC(s), %d down, %d with something to look at" % (r.get("source"), len(rows), n_dn, len(bad))]
    if r.get("stale"):
        lines.append("From SolarWinds: the snapshot is older than ad_solarwinds_max_age")
    flat = [[x[2][1], c[0], c[1], c[2], c[3]] for x in rows for d in x[5] if d.get("title") == "AD monitors (SAM) not up" for c in d["rows"]]
    extra = {"title": "From SolarWinds: AD monitors not up", "counted": False, "status": sec["status"] if flat else "ok",
             "columns": ["DC", "Component", "Status", "Value", "Message"], "rows": flat,
             "row_status": ["critical" if f[2] == "Down" else "warning" for f in flat],
             "text": "Each AD monitor (SAM) that is not up, per DC (on the website: open the DC's row)."} if flat else None
    return {"section": sec, "tile": tile, "not_read": nr, "lines": lines, "flat": extra}


def _when(x, tz):
    """A time from SolarWinds (naive UTC, or ISO) as the report shows times."""
    t = str(x or "")[:16].replace("T", " ")
    if not _utc(t):
        return t or "-"
    return _tm.local(t + " UTC", tz) if tz else t + " UTC"


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return -1


def ad_swis_insert(report, view, flat=False):
    """The report with the section after Domain controllers' health (else Domain controllers, else first), the tile after
    DCs read, and the lines in Not read. flat (the email, the workbook: no rows that open): the AD monitors not up as a
    table of their own after it."""
    rep = dict(report or {})
    v = view or {}
    secs = list(rep.get("sections") or [])
    if v.get("section"):
        sec = v["section"]
        if flat and sec["columns"] and sec["columns"][0] == "":       # no rows that open: their column and details left out
            cut = lambda xs: list(xs)[1:]                               # noqa: E731
            sec = dict(sec, columns=cut(sec["columns"]), kinds=cut(sec["kinds"]), rows=[cut(r) for r in sec["rows"]],
                       cell_status=[cut(c) for c in sec["cell_status"]],
                       text=sec["text"].replace("Open a row for the components not up, the last poll and the network card.",
                                                "The AD monitors not up: the table below."))
            sec.pop("details", None)
        at = None
        for t in AFTER:
            at = next((i + 1 for i, s in enumerate(secs) if isinstance(s, dict) and s.get("title") == t), None)
            if at is not None:
                break
        at = 1 if at is None else at
        secs = secs[:at] + [sec] + ([v["flat"]] if flat and v.get("flat") else []) + secs[at:]
    if v.get("not_read"):
        k = next((i for i, s in enumerate(secs) if isinstance(s, dict) and s.get("title") == "Not read"), None)
        if k is None:
            secs.append({"title": "Not read", "text": "What could not be read (the report is complete apart from these):", "lines": list(v["not_read"])})
        else:
            s = dict(secs[k])
            s["lines"] = list(s.get("lines") or []) + list(v["not_read"])
            secs[k] = s
    rep["sections"] = secs
    if v.get("tile"):
        summ = list(rep.get("summary") or [])            # after the first plain-number tile: the report's headline (its run
        k = next((i + 1 for i, t in enumerate(summ) if isinstance(t, dict) and _plain(t.get("value"))), len(summ))   # history) stays AD's
        rep["summary"] = summ[:k] + [v["tile"]] + summ[k:]
    return rep


def ad_swis_dcs(dcs, hostvars=None):
    """_ad.dcs -> [{dc, ip}]: the IP from the inventory's ansible_host when it is an IPv4 address (admin mode: host is the
    inventory's name; reader mode: the DC's own name - no IP, the name only)."""
    out = []
    for d in dcs or []:
        name, host, ip = str(d.get("dc") or ""), str(d.get("host") or ""), ""
        fqdn = host if "." in host and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host) else ""
        try:
            hv = hostvars[host] if hostvars is not None and host in hostvars else {}
            a = str(hv.get("ansible_host") or "") if hasattr(hv, "get") else ""
            ip = a if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", a) else ""
        except Exception:  # noqa: BLE001 - a host var that does not template: no IP, the name still matches
            ip = ""
        if name:
            out.append({"dc": name, "ip": ip, "fqdn": fqdn, "domain": str(d.get("domain") or "")})
    return out


def ad_live_block(ad, dcs, opts=None):
    """1.101.2: what the portal's Active Directory page (/portal/ad) needs to read SolarWinds for a run, kept with the run in
    the report database (ad_run.live): the DCs to find in SolarWinds, the AD problems by DC (What it means) and the From
    SolarWinds settings. No secret, no SolarWinds data: the portal reads SolarWinds itself."""
    o = opts or {}
    # dc: the DC the problem is of - an inbound replication problem ("DC02 <- DC01 (Configuration)") is the receiving DC's
    probs = [{"domain": str(p.get("domain") or ""), "object": str(p.get("object") or ""), "severity": str(p.get("severity") or ""),
              "text": str(p.get("text") or "")[:300],
              "dc": str(p.get("dc") or re.sub(r"^\s*(\S+)\s+<-\s.*$", r"\1", str(p.get("object") or ""), flags=re.S))}
             for p in (ad or {}).get("problems") or [] if isinstance(p, dict) and p.get("object")]
    return {"v": 1, "kind": "ad", "dcs": [dict(d) for d in dcs or [] if d.get("dc")], "problems": probs[:500],
            "opts": {"cpu_warn": float(o.get("cpu_warn", 90)), "mem_warn": float(o.get("mem_warn", 90)), "days": int(o.get("days", 30))}}


class FilterModule(object):
    def filters(self):
        return {"ad_swis_view": ad_swis_view, "ad_swis_insert": ad_swis_insert, "ad_swis_dcs": ad_swis_dcs, "ad_live_block": ad_live_block}
