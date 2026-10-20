# -*- coding: utf-8 -*-
"""Filters for the account lockout job (playbooks/ad_lockout.yml, roles/ad_lockout): the accounts asked
for, the DC that reads each domain, and the report - from what the scripts on the domain controllers
(roles/ad_lockout/files) answered: whether the account is locked out, and WHAT is locking it out (the
computers the bad passwords came from, ranked), with what to do. The scripts only gather facts; every
judgement is made here."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import fnmatch
import re

# a name as the service desk types it: jdoe, CORP\jdoe, jdoe@corp.example.mil. The characters an
# account name may not have (and none of LDAP's or XPath's special ones get through).
NAME_RX = re.compile(r'^(?:[A-Za-z0-9][A-Za-z0-9.-]{0,63}\\)?[^"/\\\[\]:;|=,+*?<>\x00-\x1f]{1,256}$')

KIND = {4740: "lockout", 4767: "unlock", 4771: "Kerberos", 4768: "Kerberos", 4776: "NTLM", 4625: "logon"}

# status codes -> (what it means, bad = counts towards the lockout | after = tried while locked out | other)
KERBEROS = {"0x18": ("bad password", "bad"), "0x12": ("account locked out or disabled", "after"),
            "0x17": ("password expired", "other"), "0x25": ("clock out of sync (more than 5 minutes)", "other"),
            "0x6": ("no such account", "other")}
NTSTATUS = {"0xc000006a": ("bad password", "bad"), "0xc0000234": ("account locked out", "after"),
            "0xc0000064": ("no such account", "other"), "0xc0000071": ("password expired", "other"),
            "0xc0000072": ("account disabled", "other"), "0xc000006f": ("outside its logon hours", "other"),
            "0xc0000070": ("not allowed from this computer", "other"), "0xc0000193": ("account expired", "other"),
            "0xc0000224": ("must change its password", "other"), "0xc0000133": ("clock out of sync", "other"),
            "0xc000015b": ("logon type not granted", "other")}
LOGON_TYPE = {2: "typed at the computer", 3: "network (a mapped drive, a share, an application)", 4: "batch (a scheduled task)",
              5: "a service", 7: "unlocking the screen", 8: "clear-text network sign-in (basic sign-in to a web site, an LDAP bind)",
              9: "run as another user", 10: "Remote Desktop", 11: "cached credentials"}
LOGON_HINT = {3: "something on it opens a share, a mapped drive or an application with the old password",
              4: "a scheduled task on it runs as the account (Task Scheduler, Run as)",
              5: "a service on it runs as the account (services.msc, Log On As)",
              7: "someone unlocks a locked screen on it with the old password - a session left open there",
              8: "an application or web site on it signs in with a saved password (basic sign-in, LDAP)",
              10: "a Remote Desktop session to it - sign out of the old, disconnected session"}


# ---- small helpers --------------------------------------------------------------------------------
def parse_time(v):
    if not v:
        return None
    if isinstance(v, datetime.datetime):
        t = v
    else:
        try:
            t = datetime.datetime.strptime(str(v).replace("Z", "")[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def time_text(v):
    t = parse_time(v)
    return t.strftime("%Y-%m-%d %H:%M") if t else ""


def _hours(h):
    h = float(h)
    return str(int(h)) if h == int(h) else str(h)


def short(name):
    """A computer as the events name it (\\\\PC1, pc1.corp.example.mil, PC1$) -> PC1."""
    s = str(name or "").strip().lstrip("\\").rstrip("$").strip()
    if not s or s == "-":
        return ""
    if re.match(r"^\d+\.\d+\.\d+\.\d+$", s) or ":" in s:
        return s
    return s.split(".")[0].upper()


def _matches(value, patterns):
    v = str(value or "").lower()
    return bool(v) and any(fnmatch.fnmatchcase(v, str(p).strip().lower()) for p in patterns or [] if str(p).strip())


def _status(event):
    """(what the status means, bad | after | other) of one event."""
    i = int(event.get("id") or 0)
    st = str(event.get("status") or "").lower()
    if i in (4771, 4768):
        return KERBEROS.get(st, ("Kerberos error %s" % st, "other"))
    if i == 4625 and st in ("0xc000006d", "0xc000006e") and event.get("sub_status"):
        st = str(event.get("sub_status")).lower()
    if i in (4776, 4625):
        return NTSTATUS.get(st, ("error %s" % st, "other"))
    return ("", "other")


def _interval(times):
    """'about every 5 minutes' when the attempts come at a steady pace (a saved password retrying), else ''."""
    ts = sorted(t for t in times if t)
    if len(ts) < 4:
        return ""
    gaps = sorted((b - a).total_seconds() for a, b in zip(ts, ts[1:]))
    med = gaps[len(gaps) // 2]
    if med < 20:
        return ""
    steady = sum(1 for g in gaps if abs(g - med) <= max(0.35 * med, 30))
    if steady < 0.75 * len(gaps):
        return ""
    m = med / 60.0
    return "about every %s" % ("%d seconds" % round(med) if m < 1.5 else ("%d minutes" % round(m) if m < 90 else "%.1f hours" % (m / 60)))


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


# ---- the input ------------------------------------------------------------------------------------------
def ad_lockout_names(value, max_users=25):
    """ad_lockout_user (a list, or survey text: one per line, or separated by commas or semicolons) ->
    {'names': [...], 'problems': [...]}."""
    if value is None:
        value = []
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    names, problems = [], []
    for v in value:
        s = str(v or "").strip()
        if not s:
            continue
        if not NAME_RX.match(s) or s.count("\\") > 1 or s.count("@") > 1:
            problems.append("'%s' is not an account name: give it as jdoe, CORP\\jdoe or jdoe@corp.example.mil" % s)
        elif s.lower() not in [n.lower() for n in names]:
            names.append(s)
    if len(names) > int(max_users):
        problems.append("%d accounts asked for: at most %d at a time (ad_lockout_max_users)" % (len(names), int(max_users)))
    return {"names": names, "problems": problems}


# ---- which DC reads each domain --------------------------------------------------------------------------
def ad_lockout_pick(finds):
    """{domain: host}: the PDC emulator of each domain when it answered, else the first DC whose
    ActiveDirectory module answered."""
    by, pdc = {}, {}
    for f in finds or []:
        d = f.get("data") or {}
        dom = str(d.get("domain") or "").lower()
        if not dom or not d.get("ad_module") or not d.get("netbios"):
            continue
        by.setdefault(dom, f.get("host"))
        if d.get("role") == "pdc":
            pdc.setdefault(dom, f.get("host"))
    by.update(pdc)
    return by


def _readers(finds, pick):
    hosts = set((pick or {}).values())
    return [f for f in finds or [] if f.get("host") in hosts and f.get("data")]


def ad_lockout_targets(finds, pick):
    """What each domain's DCs look for, from the reading DC's answer: {'by_domain': {domain: [account]},
    'not_found': [the names no domain has], 'accounts': [every account, with its domain]}. An account
    is {sam, upn, sid, dn, ...} as lockout_find.ps1 answered, plus domain and netbios."""
    by, found_inputs, asked, accounts = {}, set(), [], []
    for f in _readers(finds, pick):
        d = f["data"]
        dom = str(d.get("domain") or "").lower()
        rows = [u for u in (d.get("users") or []) if u.get("found")] + list(d.get("locked") or [])
        for u in d.get("users") or []:
            if u.get("input") not in asked:
                asked.append(u.get("input"))
        for u in rows:
            a = dict(u)
            a.update({"domain": dom, "netbios": d.get("netbios", ""), "reader": f.get("host")})
            found_inputs.add(str(u.get("input") or "").lower())
            if not any(x["sam"].lower() == a["sam"].lower() for x in by.get(dom, [])):
                by.setdefault(dom, []).append(a)
                accounts.append(a)
    not_found = [n for n in asked if str(n).lower() not in found_inputs]
    return {"by_domain": by, "not_found": not_found, "accounts": accounts}


# ---- the report ------------------------------------------------------------------------------------------
def _sources(acc, dcs, names, opts):
    """The computers the account's failed sign-ins came from, ranked: lockouts, then attempts, then the latest."""
    dc_short = set(short(x.get("host")) for x in dcs) | set(short((x.get("data") or {}).get("name")) for x in dcs)
    inv = opts.get("inventory") or []
    exch = opts.get("exchange_servers") or []
    src = {}
    lockouts, unlocks = [], []
    for x in dcs:
        d = x.get("data") or {}
        for e in d.get("events") or []:
            if str(e.get("user") or "").lower() != acc["sam"].lower():
                continue
            i = int(e.get("id") or 0)
            dcn = short(d.get("name") or x.get("host"))
            if i == 4767:
                unlocks.append({"time": e.get("time"), "dc": dcn, "by": e.get("by", "")})
                continue
            ip = str(e.get("ip") or "")
            comp = short(e.get("computer"))
            fq = names.get(ip, "") if ip else ""
            key = comp or (short(fq) if fq else "") or ip
            if i == 4740:
                lockouts.append({"time": e.get("time"), "dc": dcn, "computer": comp})
            s = src.setdefault(key, {"source": key, "ips": set(), "fqdn": "", "bad": 0, "after": 0, "other": 0, "lockouts": 0,
                                     "first": None, "last": None, "kinds": set(), "logon_types": set(), "processes": set(),
                                     "dcs": set(), "statuses": {}, "times": []})
            if ip:
                s["ips"].add(ip)
            if fq and not s["fqdn"]:
                s["fqdn"] = fq
            t = parse_time(e.get("time"))
            if i == 4740:
                s["lockouts"] += 1
            else:
                what, cls = _status(e)
                s[cls] += 1
                s["statuses"][what] = s["statuses"].get(what, 0) + 1
                s["kinds"].add(KIND.get(i, str(i)))
                if cls in ("bad", "after"):
                    s["times"].append(t)
                if e.get("logon_type") is not None and i == 4625:
                    s["logon_types"].add(int(e["logon_type"]))
                if e.get("process"):
                    s["processes"].add(re.split(r"[\\/]", str(e["process"]))[-1])
            s["dcs"].add(dcn)
            if t and (s["first"] is None or t < s["first"]):
                s["first"] = t
            if t and (s["last"] is None or t > s["last"]):
                s["last"] = t
    out = []
    for s in src.values():
        s["pace"] = _interval(s["times"])
        s["is_dc"] = bool(s["source"]) and s["source"] in dc_short
        s["exchange"] = any(_matches(v, exch) for v in [s["source"], s["fqdn"]] + sorted(s["ips"]))
        host = ""
        for h in inv:
            hn, addr = str(h.get("name") or ""), str(h.get("address") or "")
            if s["source"] and (short(hn) == s["source"] or (s["fqdn"] and hn.lower() == s["fqdn"])):
                host = hn
                break
            if addr and (addr in s["ips"] or (s["source"] and short(addr) == s["source"])):
                host = hn
                break
        s["inventory_host"] = host
        out.append(s)
    out.sort(key=lambda s: (-s["lockouts"], -(s["bad"] + s["after"]), -(s["last"].timestamp() if s["last"] else 0), s["source"]))
    lockouts.sort(key=lambda r: r["time"] or "", reverse=True)
    unlocks.sort(key=lambda r: r["time"] or "", reverse=True)
    return out, lockouts, unlocks


def _name(s):
    return s["source"] or "(not recorded)"


def _source_hints(acc, s, opts):
    """Plain words for one source computer."""
    n = _name(s)
    out = []
    if not s["source"]:
        out.append("The DC did not record which computer (an empty Caller Computer / Workstation): usually a server signing in "
                   "for the user - Exchange or OWA, a web application with basic sign-in, NPS / RADIUS (Wi-Fi, VPN), or a device "
                   "that is not Windows. The addresses in the Kerberos events (if any) name it.")
        return out
    if s["exchange"]:
        out.append("%s is a mail server (ad_lockout_exchange_servers): a phone or a mail app (ActiveSync, Outlook) with the old "
                   "password - update the password on the phone, or remove the account from it until then." % n)
    if s["is_dc"]:
        out.append("%s is a domain controller: the sign-ins came through it - NPS / RADIUS (Wi-Fi, VPN), an application "
                   "binding to LDAP with a saved password, or a service or task on the DC itself." % n)
    for lt in sorted(s["logon_types"]):
        if lt in LOGON_HINT:
            out.append("%s: %s." % (n, LOGON_HINT[lt]))
    if s["pace"]:
        out.append("%s tries %s: a saved password retrying, not a person typing." % (n, s["pace"]))
    if s["after"]:
        out.append("%s kept trying after the account was locked out (%s): something on it still has the old password, so the "
                   "account will lock out again after an unlock." % (n, _plural(s["after"], "time")))
    if s["processes"]:
        out.append("%s: the failed sign-ins came from %s." % (n, ", ".join(sorted(s["processes"]))))
    if not s["inventory_host"] and opts.get("check_sources"):
        out.append("%s is not in this job's inventory: check it by hand (below)." % n)
    return out


def _finding_hint(f, comp, who):
    k, nm, det = f.get("kind"), f.get("name", ""), f.get("detail", "")
    if k == "service":
        return "On %s: the service %s (%s) logs on as %s - type the new password in services.msc > Log On, or give it a service account." % (comp, nm, det, who)
    if k == "scheduled task":
        return "On %s: the scheduled task %s runs as %s - update its password in Task Scheduler (Properties > Change User or Group), or give it a service account." % (comp, nm, who)
    if k == "session":
        if det.lower().startswith("disc"):
            return "On %s: %s has a DISCONNECTED session (%s) - it keeps the old password: sign it out (quser, then logoff %s)." % (
                comp, who, det, nm.replace("session ", ""))
        return "On %s: %s is signed in (%s, %s) - lock and unlock with the new password, or sign out and in again." % (comp, who, nm, det)
    if k == "mapped drive":
        return "On %s: %s has drive %s mapped to %s - remove it and map it again with the new password (net use %s /delete)." % (comp, who, nm, det, nm)
    if k == "process":
        return "On %s: %s runs as %s (%s) - an application holding the old password: close it or sign out." % (comp, nm, who, det)
    return "On %s: %s %s %s" % (comp, k, nm, det)


def _policy_text(acc, find):
    pol = find.get("policy") or {}
    th, du, wi = pol.get("threshold"), pol.get("duration_min"), pol.get("window_min")
    which = "This domain's policy"
    if acc.get("policy"):
        th, du, wi = acc.get("threshold"), acc.get("duration_min"), acc.get("window_min")
        which = "Its fine-grained password policy %s" % acc["policy"]
    if th is None:
        return ""
    if int(th) == 0:
        return "%s never locks an account out (threshold 0)." % which
    dur = "until someone unlocks it" if du in (0, 0.0) else ("for %s minutes" % _hours(du) if du is not None else "")
    return "%s locks an account out after %d bad passwords within %s minutes, %s." % (which, int(th), _hours(wi or 0), dur)


def ad_lockout_report(finds, dcs, unlocks=None, sources=None, opts=None):
    """The account lockout report (roles/site_email layout) and the computers to check:
    {'report', 'accounts', 'check': {inventory host: [account]}, 'not_found', 'dcs_read', 'dcs_total'}."""
    o = dict(opts or {})
    hours = float(o.get("hours") or 24)
    max_rows = int(o.get("max_rows") or 50)
    pick = o.get("pick") or ad_lockout_pick(finds)
    tg = ad_lockout_targets(finds, pick)
    asked = bool(o.get("asked"))
    now = parse_time(o.get("now")) or datetime.datetime.now(datetime.timezone.utc)
    names = {}
    for x in dcs or []:
        for ip, nm in ((x.get("data") or {}).get("names") or {}).items():
            if nm and not names.get(ip):
                names[ip] = nm
    read = [x for x in dcs or [] if x.get("data")]
    finds_by_host = dict((f.get("host"), f.get("data") or {}) for f in finds or [])
    unlock_by_dom, unlock_err = {}, {}
    for u in unlocks or []:
        if u.get("error") and not ((u.get("data") or {}).get("unlocked")):
            unlock_err[u.get("domain", "")] = dict(host=u.get("host"), ok=False, error=u["error"])
        for r in ((u.get("data") or {}).get("unlocked") or []):
            unlock_by_dom[(u.get("domain", ""), str(r.get("sam", "")).lower())] = dict(r, host=u.get("host"), error=r.get("error") or u.get("error", ""))
    found_by = {}
    for s in sources or []:
        d = s.get("data") or {}
        for f in d.get("found") or []:
            found_by.setdefault(str(f.get("user", "")).lower(), []).append(dict(f, host=s.get("host"), computer=short(d.get("name") or s.get("host"))))

    sections, accounts, check, problems_unread = [], [], {}, []
    overview = []
    for acc in tg["accounts"]:
        who = "%s\\%s" % (acc.get("netbios") or acc["domain"], acc["sam"])
        find = finds_by_host.get(acc.get("reader"), {})
        srcs, locks, unl = _sources(acc, [x for x in read if str((x.get("data") or {}).get("domain") or "").lower() == acc["domain"]], names, o)
        per_dc = []
        for x in dcs or []:
            d = x.get("data") or {}
            if d and str(d.get("domain") or "").lower() != acc["domain"]:
                continue
            st = next((u for u in d.get("users") or [] if str(u.get("sam", "")).lower() == acc["sam"].lower()), None)
            per_dc.append((short(d.get("name") or x.get("host")), x, st, d.get("role") == "pdc", acc["sam"] in (d.get("capped") or [])))
        locked = bool(acc.get("locked"))
        ul = unlock_by_dom.get((acc["domain"], acc["sam"].lower())) or (unlock_err.get(acc["domain"]) if locked else None)
        bad = sum(s["bad"] for s in srcs)
        after = sum(s["after"] for s in srcs)
        top = next((s for s in srcs if s["lockouts"] or s["bad"] or s["after"]), None)

        # ---- what to do ----
        lines = []
        if ul and ul.get("ok"):
            lines.append("UNLOCKED by this job on %s. It locks out again as soon as the source below tries its old password again: fix that first." % short(ul.get("host")))
        elif ul:
            lines.append("NOT unlocked: %s" % (ul.get("error") or "no answer"))
        elif o.get("unlock") and locked and o.get("check_mode"):
            lines.append("Would be unlocked (dry run): nothing was changed.")
        elif o.get("unlock") and not locked:
            lines.append("Not locked out: nothing to unlock.")
        if locked and ul and ul.get("ok"):
            lines.append("%s was locked out since %s UTC%s." % (who, time_text(acc.get("lockout_time")) or "?",
                                                               (" (recorded by %s)" % locks[0]["dc"]) if locks else ""))
        elif locked:
            lines.append("%s is LOCKED OUT since %s UTC%s." % (who, time_text(acc.get("lockout_time")) or "?",
                                                              (" (recorded by %s)" % locks[0]["dc"]) if locks else ""))
        else:
            lines.append("%s is not locked out now%s." % (who, (", unlocked %s UTC by %s" % (time_text(unl[0]["time"]), unl[0]["by"] or "?")) if unl else ""))
        pt = _policy_text(acc, find)
        if pt:
            lines.append(pt)
        if not acc.get("enabled", True):
            lines.append("The account is DISABLED: sign-ins fail whatever the password.")
        if acc.get("password_expired"):
            lines.append("Its password has EXPIRED: the user must change it before anything else works.")
        top_line = ""
        if top:
            top_line = "Most bad passwords came from %s%s: %s%s in the last %s h, the last at %s UTC%s." % (
                _name(top), (" (%s)" % ", ".join(sorted(top["ips"]))) if top["ips"] and top["source"] not in top["ips"] else "",
                _plural(top["bad"] + top["after"], "failed sign-in"), (", %s" % _plural(top["lockouts"], "lockout")) if top["lockouts"] else "",
                _hours(hours), time_text(top["last"]), (", " + top["pace"]) if top["pace"] else "")
            lines.append(top_line)
            for s in [x for x in srcs if x["lockouts"] or x["bad"] or x["after"]][:3]:
                lines += _source_hints(acc, s, o)
        elif locked or any(st and st.get("bad_pwd_count") for _, _, st, _, _ in per_dc):
            lines.append("No failed sign-in for %s in the Security logs of the last %s h: the DCs may not audit Account Lockout, Kerberos "
                         "Authentication Service and Credential Validation failures (Advanced Audit Policy), or the events are older "
                         "(ad_lockout_hours) or rolled out of the Security log." % (acc["sam"], _hours(hours)))
        else:
            lines.append("No failed sign-in in the last %s h." % _hours(hours))
        for f in found_by.get(acc["sam"].lower(), []):
            lines.append(_finding_hint(f, f["computer"], who))
        if top or locked:
            lines.append("To check a computer by hand: services (Log On As), Task Scheduler (Run as), Credential Manager (Windows "
                         "Credentials) of the user, mapped drives (net use), disconnected Remote Desktop sessions (quser), "
                         "applications with a saved password (Outlook, VPN, a phone's mail or Wi-Fi). On the user's own PC: lock it, "
                         "and unlock it with the new password.")
        for dcn, _, _, _, capped in per_dc:
            if capped:
                lines.append("%s had more events for %s than ad_lockout_max_events: only the newest are counted." % (dcn, acc["sam"]))

        # the computers to look at (ad_lockout_check_sources)
        if o.get("check_sources"):
            for s in srcs[:int(o.get("check_max") or 5)]:
                if s["inventory_host"] and (s["lockouts"] or s["bad"] or s["after"]):
                    lst = check.setdefault(s["inventory_host"], [])
                    if not any(a["sam"] == acc["sam"] and a["domain"] == acc["domain"] for a in lst):
                        lst.append({"sam": acc["sam"], "domain": acc["domain"], "netbios": acc.get("netbios", ""),
                                    "upn": acc.get("upn", ""), "sid": acc.get("sid", "")})

        rec = {"account": who, "domain": acc["domain"], "display": acc.get("display", ""), "locked": locked,
               "lockout_time": acc.get("lockout_time", ""), "bad": bad, "after": after,
               "unlocked": bool(ul and ul.get("ok")),
               "caller": (locks[0]["computer"] or "(not recorded)") if locks else "",
               "main": ("%s%s" % (_name(top), (" (%s)" % ", ".join(sorted(top["ips"]))) if top["ips"] and top["source"] not in top["ips"] else "")) if top else "", "unlock_error": (ul.get("error") if ul and not ul.get("ok") else ""),
               "sources": [{"source": _name(s), "addresses": sorted(s["ips"]), "bad": s["bad"], "after": s["after"],
                            "lockouts": s["lockouts"], "last": time_text(s["last"]), "inventory_host": s["inventory_host"]} for s in srcs],
               "lines": lines}
        accounts.append(rec)
        overview.append((acc, who, srcs, rec))

        if asked:
            st_acc = "info" if rec["unlocked"] else ("warning" if locked else "")
            facts = [["Name", acc.get("display") or "-"], ["Locked out", ("YES, since %s UTC" % time_text(acc.get("lockout_time"))) if locked else "no"],
                     ["Enabled", "yes" if acc.get("enabled", True) else "NO"],
                     ["Password last set", (time_text(acc.get("password_set")) + " UTC") if acc.get("password_set") else "-"],
                     ["Password", "EXPIRED" if acc.get("password_expired") else ("never expires" if acc.get("password_never_expires") else "valid")],
                     ["UPN", acc.get("upn") or "-"]]
            sections.append({"title": "%s: what to do" % who, "status": st_acc or "ok", "lines": lines})
            sections.append({"title": "%s%s" % (who, (" - " + acc["display"]) if acc.get("display") else ""),
                             "columns": ["What", "Now"], "rows": facts,
                             "cell_status": [["", "warning" if (r[0] == "Locked out" and locked) or r[1] in ("NO", "EXPIRED") else ""] for r in facts],
                             "row_status": ["" for _ in facts]})
            rows, rs, cs = [], [], []
            for s in srcs[:max_rows]:
                how = sorted(s["kinds"]) + [LOGON_TYPE.get(t, "logon type %d" % t) for t in sorted(s["logon_types"])]
                stx = ", ".join("%s %s" % (v, k) for k, v in sorted(s["statuses"].items(), key=lambda kv: -kv[1]) if k)
                rows.append([_name(s), ", ".join(sorted(s["ips"])) or "-", s["bad"], s["after"], s["lockouts"], time_text(s["first"]), time_text(s["last"]),
                             s["pace"] or "-", "; ".join(how) + ((" - " + stx) if stx else ""), ", ".join(sorted(s["dcs"])),
                             s["inventory_host"] or ("-" if not s["source"] else "not in it")])
                hot = s is top or s["lockouts"] > 0
                rs.append("warning" if hot else "")
                cs.append(["warning" if hot else ""] + [""] * 10)
            sec = {"title": "%s: where the failed sign-ins came from (last %s h)" % (who, _hours(hours)),
                   "text": ("Each computer or address the DCs saw a failed sign-in from, the ones that locked it out first. "
                            "'After the lock' = it kept trying while the account was locked out.")}
            if rows:
                sec.update({"columns": ["Source", "Address", "Bad passwords", "After the lock", "Lockouts", "First", "Last", "Pace", "How",
                                        "Seen by", "In the inventory"], "rows": rows, "row_status": rs, "cell_status": cs})
                if top:
                    sec["status"] = "warning"
            else:
                sec["text"] += " None in the last %s h." % _hours(hours)
            if len(srcs) > max_rows:
                sec["text"] += " The first %d of %d are shown; the job's artifacts (ad_lockout) have all." % (max_rows, len(srcs))
            sections.append(sec)
            ev = [[time_text(r["time"]), r["dc"], "locked out", r["computer"] or "(not recorded)"] for r in locks] + \
                 [[time_text(r["time"]), r["dc"], "unlocked", r["by"] or "?"] for r in unl]
            ev.sort(key=lambda r: r[0], reverse=True)
            sections.append({"title": "%s: lockouts and unlocks (last %s h)" % (who, _hours(hours)),
                             "text": "Event 4740 (locked out, with the caller computer) and 4767 (unlocked, by whom)." + ("" if ev else " None."),
                             **({"columns": ["When (UTC)", "DC", "What", "Computer / by"], "rows": ev[:max_rows],
                                 "row_status": ["warning" if r[2] == "locked out" else "" for r in ev[:max_rows]],
                                 "cell_status": [["", "", "", ""] for _ in ev[:max_rows]]} if ev else {})})
            drows, dst = [], []
            for dcn, x, st, is_pdc, _ in per_dc:
                if not x.get("data"):
                    drows.append([dcn, "UNREACHABLE" if not x.get("reachable", True) else "NOT READ", "", "", "", ""])
                    dst.append("critical")
                    continue
                if not st or not st.get("read"):
                    drows.append([dcn + (" (PDC)" if is_pdc else ""), "not read", "", "", "", ""])
                    dst.append("warning")
                    continue
                drows.append([dcn + (" (PDC)" if is_pdc else ""), st.get("bad_pwd_count") if st.get("bad_pwd_count") is not None else "",
                              (time_text(st.get("bad_password_time")) or "-"), "yes" if st.get("locked") else "no",
                              time_text(st.get("last_logon")) or "-", ""])
                dst.append("warning" if st.get("bad_pwd_count") else "")
            sections.append({"title": "%s: on each domain controller" % who,
                             "text": "badPwdCount and the last bad password are NOT replicated: each DC counts the bad passwords it was "
                                     "given itself, and the PDC emulator is told of each one. Last logon is this DC's own.",
                             "columns": ["DC", "Bad passwords now", "Last bad password", "Locked out", "Last logon", ""],
                             "rows": drows, "row_status": dst, "cell_status": [[s] + [""] * 5 for s in dst]})
            frows = [[f["computer"], f.get("kind", ""), f.get("name", ""), f.get("detail", "")] for f in found_by.get(acc["sam"].lower(), [])]
            if o.get("check_sources"):
                checked = sorted(set(h for h, lst in check.items() if any(a["sam"] == acc["sam"] for a in lst)))
                sections.append({"title": "%s: on the source computers" % who,
                                 "text": ("Services, scheduled tasks, sessions, processes and mapped drives of the account on %s." % ", ".join(checked)
                                          if checked else "No source computer is a host of this job's inventory: nothing was checked.")
                                 + ("" if frows or not checked else " Nothing runs as the account there."),
                                 **({"columns": ["Computer", "What", "Name", "Detail"], "rows": frows,
                                     "row_status": ["warning" for _ in frows], "cell_status": [["", "", "", ""] for _ in frows]} if frows else {})})

    if not asked:
        rows, rs = [], []
        for acc, who, srcs, rec in overview:
            hot = [s for s in srcs if s["lockouts"] or s["bad"] or s["after"]]
            rows.append([who, acc.get("display") or "", time_text(acc.get("lockout_time")), ", ".join(
                "%s (%d)" % (_name(s), s["bad"] + s["after"]) for s in hot[:3]) or "-", rec["bad"] + rec["after"],
                rec["caller"] or "-"])
            rs.append("warning")
        total = sum(int((f or {}).get("locked_total") or 0) for f in [finds_by_host.get(h) for h in pick.values()])
        text = ("Every account locked out now, the latest first, with the computers its failed sign-ins came from (the most first). "
                "For the detail of one, run again with ad_lockout_user: its name.")
        if total > len(rows):
            text += " %d are locked out; the latest %d are shown (ad_lockout_max_users)." % (total, len(rows))
        sec = {"title": "Locked out now", "text": text if rows else "No account is locked out."}
        if rows:
            sec.update({"columns": ["Account", "Name", "Locked out since (UTC)", "Sources (failed sign-ins)", "Failed sign-ins (%s h)" % _hours(hours), "Lockout caller (4740)"],
                        "rows": rows, "row_status": rs, "cell_status": [["warning", "", "", "", "", ""] for _ in rows], "status": "warning"})
        sections.insert(0, sec)
        for acc, who, srcs, rec in overview:
            hints = [ln for ln in rec["lines"] if not ln.startswith("To check a computer")]
            sections.append({"title": who, "status": "warning", "lines": hints[:8]})

    # ---- what could not be read ----
    notes = []
    for x in dcs or []:
        d = x.get("data") or {}
        nm = short(d.get("name") or x.get("host"))
        if not d:
            notes.append("%s: %s" % (nm, ("UNREACHABLE - " if not x.get("reachable", True) else "") + (x.get("error") or "no answer")))
        notes += ["%s: %s" % (nm, e) for e in d.get("errors") or []]
    for f in finds or []:
        d = f.get("data") or {}
        if f.get("host") in pick.values():
            notes += ["%s (accounts): %s" % (short(d.get("name") or f.get("host")), e) for e in d.get("errors") or []]
    for s in sources or []:
        d = s.get("data") or {}
        nm = short(d.get("name") or s.get("host"))
        if not d:
            notes.append("%s (source computer): %s" % (nm, ("UNREACHABLE - " if not s.get("reachable", True) else "") + (s.get("error") or "no answer")))
        notes += ["%s (source computer): %s" % (nm, e) for e in d.get("errors") or []]
    if notes:
        sections.append({"title": "Not read", "text": "What could not be read (the report is complete apart from these):", "lines": notes[:100]})

    n_locked = sum(1 for a in accounts if a["locked"] and not a["unlocked"])
    doms = ", ".join(sorted(set(pick.keys()))) or "no domain read"
    if asked and len(accounts) == 1:
        a, top = accounts[0], next((s for s in accounts[0]["sources"] if s["lockouts"] or s["bad"] or s["after"]), None)
        frm = (" - from %s" % top["source"]) if top else ""
        if a["unlocked"]:
            title = "Account lockout: %s unlocked by this job%s" % (a["account"], frm)
        elif a["locked"]:
            title = "Account lockout: %s LOCKED OUT%s" % (a["account"], frm)
        else:
            title = "Account lockout: %s not locked out%s" % (a["account"], (" - %s in %s h" % (_plural(a["bad"] + a["after"], "failed sign-in"), _hours(hours))) if a["bad"] + a["after"] else "")
    elif asked:
        title = "Account lockout: %d of %d account(s) locked out (%s)" % (n_locked, len(accounts), ", ".join(a["account"] for a in accounts)[:120])
    else:
        title = ("Account lockout: %d account(s) locked out now (%s)" % (len(accounts), doms)) if accounts else ("Account lockout: no account locked out (%s)" % doms)
    status = "warning" if n_locked else ("info" if any(a["unlocked"] for a in accounts) else "ok")
    srcs_all = set(s["source"] for a in accounts for s in a["sources"] if s["bad"] or s["after"] or s["lockouts"])
    summary = [{"label": "Locked out", "value": n_locked, "status": "warning" if n_locked else "ok"},
               {"label": "Failed sign-ins", "value": sum(a["bad"] + a["after"] for a in accounts), "status": "info" if any(a["bad"] for a in accounts) else "ok"},
               {"label": "Source computers", "value": len(srcs_all), "status": "info" if srcs_all else "ok"},
               {"label": "DCs read", "value": "%d of %d" % (len(read), len(dcs or [])), "status": "ok" if len(read) == len(dcs or []) else "critical"}]
    report = {"title": title, "status": status, "summary": summary, "sections": sections,
              "subtitle": "Read %s UTC - the Security logs of the last %s h" % (now.strftime("%Y-%m-%d %H:%M"), _hours(hours))}
    return {"report": report, "accounts": accounts, "check": check, "not_found": tg["not_found"],
            "dcs_read": len(read), "dcs_total": len(dcs or [])}


class FilterModule(object):
    def filters(self):
        return {"ad_lockout_names": ad_lockout_names, "ad_lockout_pick": ad_lockout_pick,
                "ad_lockout_targets": ad_lockout_targets, "ad_lockout_report": ad_lockout_report}
