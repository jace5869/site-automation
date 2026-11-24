# -*- coding: utf-8 -*-
"""The report of playbooks/win_local_groups.yml (1.122.0): who is in the privileged local groups (Administrators, Remote
Desktop Users, Remote Management Users, Backup Operators - win_local_groups) of each Windows server, from what
roles/win_local_groups/files/win_local_groups.ps1 printed on each one. Read only. The model, each server's findings (an
enabled local account that is no built-in one in Administrators: warning; a member found on few of the servers: low), the
report's sections (Unusual members, By server, By member, Not read), the page's Group and Member filters, the workbook."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

from collections import defaultdict
from decimal import Decimal, InvalidOperation

import csv
import importlib.util
import io
import json
import os
import re

# the answer keys' block writer (plugins/filter/stig_answer_filters.py: the <AnswerKey> block, its indent under <Vuln>),
# loaded by its path as the filters load each other - the answer file here is written exactly as that work writes one
_sa_spec = importlib.util.spec_from_file_location("stig_answer_filters_for_win_groups", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "stig_answer_filters.py"))
sa = importlib.util.module_from_spec(_sa_spec)
_sa_spec.loader.exec_module(sa)

MARK = "###SITE-JSON### "
ADMINS = "S-1-5-32-544"
# the built-in local accounts (their RID): Administrator, Guest, DefaultAccount, WDAGUtilityAccount
BUILTIN_RIDS = ("500", "501", "503", "504")
WELL_KNOWN = {"S-1-5-32-544": "Administrators", "S-1-5-32-555": "Remote Desktop Users", "S-1-5-32-580": "Remote Management Users",
              "S-1-5-32-551": "Backup Operators", "S-1-5-32-549": "Server Operators", "S-1-5-32-550": "Print Operators",
              "S-1-5-32-548": "Account Operators",
              # 1.122.2: every SID the read knows (win_local_groups.ps1 $known): one group on any language of Windows
              "S-1-5-32-547": "Power Users", "S-1-5-32-545": "Users", "S-1-5-32-546": "Guests",
              "S-1-5-32-556": "Network Configuration Operators", "S-1-5-32-573": "Event Log Readers",
              "S-1-5-32-562": "Distributed COM Users", "S-1-5-32-578": "Hyper-V Administrators"}
# the DISA rules "only administrators responsible for the system may have Administrator rights" (U_MS_Windows_Server_*
# XCCDF: 2019 V3R9, 2022 V2R11, 2025 V1R4) - the default of win_local_groups_stig_rules
STIG_RULES = [{"stig": "WinServer2019", "role": "member", "rule": "WN19-MS-000010", "vuln": "V-205746"},
              {"stig": "WinServer2019", "role": "dc", "rule": "WN19-DC-000010", "vuln": "V-205738"},
              {"stig": "WinServer2022", "role": "member", "rule": "WN22-MS-000010", "vuln": "V-254428"},
              {"stig": "WinServer2022", "role": "dc", "rule": "WN22-DC-000010", "vuln": "V-254385"},
              {"stig": "WinServer2025", "role": "member", "rule": "WN25-MS-000010", "vuln": "V-278177"},
              {"stig": "WinServer2025", "role": "dc", "rule": "WN25-DC-000010", "vuln": "V-278132"}]
# the page's Export selected (site-report.js): CSV, JSON and the approved list's YAML of the ticked rows
SELECT_BASE = {"setting": "win_local_groups_approved", "kind": "groups", "name": "local-groups",
               "note": "For xlsx, use the workbook's Approve column. The answer file and the PowerShell check take the ticked "
                       "Administrators rows only."}
MIN_SERVERS = 3          # fewer servers read: no member is called unusual (every one is on "most" of them)
NOTE = ("Read only: the job read each group's members and changed nothing. A group is found by its well-known SID (S-1-5-32-544 "
        "is Administrators on any language of Windows), else by its name. A member Windows cannot name is listed as "
        "\"unresolved SID\", never left out. Domain groups are not expanded yet: their members are in AD.")


def win_local_groups_parse(stdout_lines):
    """The script's output lines -> its result ({computer, domain, groups}), or {"error": why} when there is none."""
    lines = [str(x) for x in (stdout_lines or []) if str(x).startswith(MARK)]
    if not lines:
        return {"error": "the script printed no result"}
    try:
        r = json.loads(lines[-1][len(MARK):])
    except ValueError as e:
        return {"error": "the script's result is not JSON: %s" % e}
    if not isinstance(r, dict):
        return {"error": "the script's result is not an object"}
    return r


def _short(host):
    h = str(host)
    return h if re.match(r"^[0-9.]+$", h) or ":" in h else h.split(".")[0]


def _rid(sid):
    return str(sid or "").rsplit("-", 1)[-1]


def _builtin(m):
    return m["source"] == "Local" and str(m["sid"]).startswith("S-1-5-21-") and _rid(m["sid"]) in BUILTIN_RIDS


def _member(raw, computer):
    """One member of the script -> {key, name, sid, type, source, enabled, last_logon, builtin}. A local account's name
    loses its server's name (SRV01\\svc_x -> .\\svc_x) so the same local name on two servers is one member."""
    sid = str(raw.get("sid") or "")
    name = str(raw.get("name") or "") or ("unresolved SID " + sid)
    src = str(raw.get("source") or "")
    if src not in ("Local", "Domain", "AzureAD", "Unresolved"):
        src = "Unresolved" if name.startswith("unresolved SID ") else "Local"
    if name.startswith("unresolved SID "):
        src = "Unresolved"
    shown = name
    if src == "Local" and "\\" in name and name.split("\\", 1)[0].lower() == str(computer or "").lower():
        shown = ".\\" + name.split("\\", 1)[1]
    typ = str(raw.get("type") or "") or "Unknown"
    m = {"name": shown, "raw": name, "sid": sid, "type": typ, "source": src, "enabled": raw.get("enabled"), "last_logon": str(raw.get("last_logon") or "")}
    # the key that makes "the same member" across servers: a domain or unresolved one by its SID, a local one by its name
    m["key"] = ("local:" + shown.lower()) if src == "Local" and not shown.startswith("NT AUTHORITY") else ("sid:" + sid if sid else "name:" + shown.lower())
    if src == "Local" and str(sid).startswith("S-1-5-32-"):
        m["key"] = "sid:" + sid
    m["builtin"] = _builtin(m)
    return m


def _pk(m, g):
    """A (member, group) pair's key in the model's pairs: a string, so the model is plain JSON (an Ansible fact)."""
    return "%s|%s" % (g["key"], m["key"])


def _gkey(sid, name):
    """A group's key (a filter button's value: no space): a well-known group by its SID, any other by its whole name
    (lower case, hex - Help Desk and Help-Desk stay two groups)."""
    if sid:
        return "g" + sid.lower()
    return "gx" + str(name).lower().encode("utf-8").hex()


def win_local_groups_model(hosts, usual="off", baseline_pct=90, rare_pct=10):
    """hosts: [{host, result (the parsed script output, {} when none), unreached (why, when it was not reached)}] ->
    the model: servers read and not, each group's members on each server, each (member, group)'s servers, the usual ones.
    usual: off (the default: no member is called unusual), auto (on 80% or more of the servers that have the group), or a
    percent (e.g. 60)."""
    read, unread = [], []
    for h in hosts or []:
        name = str(h.get("host") or "")
        r = h.get("result") or {}
        if h.get("unreached"):
            unread.append({"host": name, "why": "not reached: %s" % h["unreached"]})
            continue
        if not r or r.get("error") or not isinstance(r.get("groups"), list):
            unread.append({"host": name, "why": str(r.get("error") or "no result - the job failed on it before the read")})
            continue
        comp = str(r.get("computer") or _short(name))
        groups = []
        for g in r["groups"]:
            sid = str(g.get("sid") or "")
            gname = str(g.get("name") or "")
            label = WELL_KNOWN.get(sid) or gname or str(g.get("asked") or sid)
            # a read that failed part way (ADSI stopped) keeps the members it found: shown, exported, never Pass
            partial = not g.get("found") and bool(g.get("members"))
            groups.append({"key": _gkey(sid if sid in WELL_KNOWN else "", label), "label": label, "local_name": gname, "sid": sid,
                           "asked": str(g.get("asked") or ""), "found": bool(g.get("found")) or partial, "partial": partial,
                           "method": str(g.get("method") or ""),
                           "error": str(g.get("error") or ""),
                           "members": sorted((_member(m, comp) for m in g.get("members") or [] if isinstance(m, dict)),
                                             key=lambda m: (m["source"] != "Unresolved", m["name"].lower()))})
        read.append({"host": name, "computer": comp, "domain": str(r.get("domain") or ""), "groups": groups,
                     "os": str(r.get("os") or ""), "dc": _is_dc(r.get("domain_role")), "inv_groups": list(h.get("inv_groups") or [])})
    # each group (by its key), in the order the setting asked for them
    order, labels = [], {}
    for s in read:
        for g in s["groups"]:
            if g["key"] not in labels:
                order.append(g["key"])
                labels[g["key"]] = g["label"]
    have = dict((k, [s["host"] for s in read if any(g["key"] == k and g["found"] for g in s["groups"])]) for k in order)
    pairs = {}
    for s in read:
        for g in s["groups"]:
            for m in g["members"]:
                p = pairs.setdefault(_pk(m, g), {"member": m, "group": g["key"], "label": g["label"], "hosts": [], "admin_hosts": [], "sids": {}})
                p["hosts"].append(s["host"])
                p["sids"][s["host"]] = m["sid"]
                if _local_admin(m, g["key"]):
                    p["admin_hosts"].append(s["host"])
                if p["member"]["source"] == "Local" and m.get("enabled") and not p["member"].get("enabled"):
                    p["member"] = m
    pct = None
    if str(usual).strip().lower() not in ("off", "false", "no", "0", ""):
        try:
            pct = 80.0 if str(usual).strip().lower() == "auto" else float(usual)
        except ValueError:
            pct = 80.0
    for p in pairs.values():
        n = len(have.get(p["group"]) or [])
        p["of"] = n
        p["unusual"] = bool(pct is not None and n >= MIN_SERVERS and len(p["hosts"]) * 100.0 < pct * n)
        p["local_admin"] = bool(p["admin_hosts"])        # on the servers where this server's own account is enabled
    shorts = [_short(x) for x in [s["host"] for s in read] + [u["host"] for u in unread]]
    shown = dict((h, _short(h) if shorts.count(_short(h)) == 1 else h) for h in [s["host"] for s in read] + [u["host"] for u in unread])
    model = {"shown": shown, "read": read, "unread": unread, "groups": [{"key": k, "label": labels[k], "servers": len(have[k])} for k in order],
             "pairs": pairs, "usual_pct": pct, "total": len(read) + len(unread)}
    model.update(_prevalence(read, model["groups"], baseline_pct, rare_pct))
    return model


def _display_pct(count, total):
    """Round half up, reserving 100 for unanimous membership."""
    return 100 if count == total else min(99, (200 * count + total) // (2 * total))


def _threshold(value, default, setting):
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal(default)
    message = "%s must be a finite number between 0 and 100" % setting
    if isinstance(value, bool):
        raise ValueError(message)
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(message) from None
    if not number.is_finite() or not 0 <= number <= 100:
        raise ValueError(message)
    return number


def _prevalence(read, groups, baseline_pct, rare_pct):
    baseline = _threshold(baseline_pct, 90, "win_local_groups_baseline_pct")
    rare = _threshold(rare_pct, 10, "win_local_groups_rare_pct")
    # Count distinct servers per member identity and per candidate display name.
    observed = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    sids = defaultdict(set)
    for server in read:
        for group in server["groups"]:
            for member in group["members"]:
                name = (member["source"] == "Unresolved", member["name"])
                key = (group["key"], member["key"])
                observed[key[0]][key[1]][name].add(server["host"])
                sids[key].add(member["sid"])

    total = len(read)
    prevalence = []
    for group in sorted(groups, key=lambda g: g["label"].lower() != "administrators"):
        rows = []
        for member_key, spellings in observed[group["key"]].items():
            unresolved, name = min(spellings, key=lambda n: (n[0], -len(spellings[n]), n[1]))
            hosts = sorted(set().union(*spellings.values()))
            count = len(hosts)
            member_sids = sids[group["key"], member_key]
            shared_sid = next(iter(member_sids)) if len(member_sids) == 1 else ""
            entry = shared_sid if unresolved else name
            if entry.startswith(".\\"):
                entry = "{computer}" + entry[1:]
            rows.append({"member": name, "count": count, "pct": _display_pct(count, total),
                         "hosts": hosts, "rare": count * 100 <= rare * total, "baseline": count * 100 >= baseline * total,
                         "entry": literal_entry(entry, shared_sid)})
        if rows:
            prevalence.append(dict(group, members=sorted(rows, key=lambda r: (-r["count"], r["member"].lower()))))
    return {"prevalence": prevalence, "baseline_pct": float(baseline) if baseline % 1 else int(baseline),
            "rare_pct": float(rare) if rare % 1 else int(rare)}


def win_local_groups_baseline(model, generated):
    groups = {}
    for group in model["prevalence"]:
        rows = [{k: row[k] for k in ("member", "count", "pct")} for row in group["members"] if row["baseline"]]
        if rows:
            groups[group["label"]] = rows
    return {"generated": generated, "servers_answered": len(model["read"]), "threshold_pct": model["baseline_pct"], "groups": groups}


def _baseline_yaml(model):
    entries = [r["entry"] for g in model["prevalence"] if g["label"].lower() == "administrators"
               for r in g["members"] if r["baseline"]]
    value = {"groups": {"Administrators": [e for e in entries if e]}}
    # JSON flow syntax keeps even names containing quotes or newlines on one line in AAP.
    note = ""
    if "" in entries:
        note += "# Omitted entries containing template marks without a shared SID. Review the JSON.\n"
    block = ["win_local_groups_approved:", "  groups:", "    Administrators:"]
    block.extend("      - " + json.dumps(e, ensure_ascii=False) for e in entries if e)
    if not any(entries):
        block[-1] += " []"
    flow = "win_local_groups_approved: " + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return note, flow, "\n".join(block)


def win_local_groups_baseline_snippet(model):
    note, flow, block = _baseline_yaml(model)
    return "# Review the suggested Administrators before pasting either form into AAP.\n" + note + flow + "\n---\n# Block form\n" + block + "\n"


def _is_dc(role):
    """Win32_ComputerSystem.DomainRole: 4 and 5 are domain controllers."""
    try:
        return int(role) >= 4
    except (TypeError, ValueError):
        return False


def _local_admin(m, gkey):
    """An enabled local user, no built-in one, in Administrators."""
    return (gkey == _gkey(ADMINS, "") and m["source"] == "Local" and not m["builtin"] and m.get("enabled") is True
            and str(m["sid"]).startswith("S-1-5-21-"))


def _kind(m):
    if m["source"] == "Unresolved":
        return "Unresolved SID"
    if m["source"] == "Local" and str(m["sid"]).startswith("S-1-5-21-") and m["type"] == "User":
        st = "enabled" if m.get("enabled") is True else ("disabled" if m.get("enabled") is False else "")
        return "Local user" + (" - " + st if st else "") + (" (built-in)" if m["builtin"] else "")
    if m["source"] == "Domain":
        return "Domain group (not expanded)" if m["type"] == "Group" else "Domain " + m["type"].lower()
    if m["source"] == "AzureAD":
        return "Azure AD " + m["type"].lower()
    return "Local " + m["type"].lower()


def _note(p, m=None):
    m = m or p["member"]
    out = []
    if _local_admin(m, p["group"]):
        out.append("enabled local account in Administrators")
    if p["unusual"]:
        out.append("only on %d of %d servers" % (len(p["hosts"]), p["of"]))
    return "; ".join(out)


def win_local_groups_findings(model, host):
    """One server's findings (site_findings): warning - an enabled local account, no built-in one, in Administrators;
    warning - a group the script could not read. An unusual member is no finding of its own: it is the report's first
    section (Unusual members), so it is not listed twice."""
    out = []
    s = next((x for x in model["read"] if x["host"] == host), None)
    if not s:
        return out
    for g in s["groups"]:
        if g.get("partial"):
            out.append({"id": "local_groups:%s:partial" % g["key"], "check": "local groups", "severity": "warning",
                        "summary": "%s: partial read - %s" % (g["label"], g["error"] or "the read stopped part way")})
        if not g["found"] and g["error"] != "no such local group on this server":
            out.append({"id": "local_groups:%s:not-read" % g["key"], "check": "local groups", "severity": "warning",
                        "summary": "%s: not read - %s" % (g["label"], g["error"])})
        for m in g["members"]:
            p = model["pairs"][_pk(m, g)]
            if _local_admin(m, g["key"]):
                out.append({"id": "local_groups:local-admin:%s" % m["name"].lower(), "check": "local groups", "severity": "warning",
                            "summary": "%s is an enabled local account in Administrators" % m["name"],
                            "fix": "If it is not needed as an administrator, take it out of Administrators or disable it - with your change process."})
    return out


def _entry(p):
    """A member as an approved-list entry: a local account on more than one server as {computer}\\name (one entry for all
    of them), else its name as Windows gives it; an unresolved one by its SID."""
    m = p["member"]
    if m["source"] == "Unresolved":
        return m["sid"]
    if m["name"].startswith(".\\"):
        return ("{computer}\\" + m["name"][2:]) if len(p["hosts"]) > 1 else m["raw"]
    return m["raw"]


TEMPLATE_MARKS = ("{{", "{%", "{#", "}}", "%}", "#}")


def literal_entry(entry, sid):
    """An entry safe to paste into an Ansible variable: one with a template mark ({{ {% {#) would be run by Ansible, so
    it becomes its SID; without a SID it is '' (left out, said in a comment)."""
    if any(t in str(entry) for t in TEMPLATE_MARKS):
        return sid if sid and not any(t in str(sid) for t in TEMPLATE_MARKS) else ""
    return entry


def _sel(server, p, m=None):
    """A row's data for the page's Export selected (site-report.js): JSON in data-sel. A By server row (server given) is
    that server's member: its own name as the entry (SRV01\\svc approves it there only); a By member row is the member
    on all its servers: the shared entry ({computer}\\svc) and each server's SID (x). 1.122.2: a By server row (server: the
    read's server) also carries the server's full name (f, as the checks compare it) and computer name (c): its checks
    approve the entry on that server only - a domain account ticked on one server is never approved on all of them."""
    m = m or p["member"]
    if server:
        srv = server if isinstance(server, dict) else {"host": str(server), "computer": "", "domain": ""}
        e = m["sid"] if m["source"] == "Unresolved" else m["raw"]
        d = {"g": p["label"], "e": literal_entry(e, m["sid"]), "m": m["name"], "i": m["sid"], "k": _kind(m), "n": len(p["hosts"]), "s": srv["host"],
             "f": _fqdn(srv) if srv.get("computer") else "", "c": str(srv.get("computer") or "")}
    else:
        sids = p.get("sids") or {}
        d = {"g": p["label"], "e": literal_entry(_entry(p), m["sid"] if len(set(sids.values())) <= 1 else ""), "m": m["name"],
             "i": m["sid"] if len(set(sids.values())) <= 1 else "", "k": _kind(m), "n": len(p["hosts"]),
             "x": dict((h, sids[h]) for h in sorted(sids))}
    return json.dumps(d, separators=(",", ":"), sort_keys=True)


def _shown(model, host):
    return (model.get("shown") or {}).get(host) or str(host)


def _tag(gkeys, names, sel=None):
    t = {"pf": " ".join(gkeys), "h": " ".join(names).lower()}
    if sel:
        t["data"] = {"sel": sel}
    return t


def win_local_groups_sections(model, web=True, rules=None):
    """The summary, prevalence tables and inventory. The email previews By server and By member."""
    read, unread, pairs = model["read"], model["unread"], model["pairs"]
    akey = _gkey(ADMINS, "")
    adm = [p for p in pairs.values() if p["group"] == akey]
    local_adm = [p for p in adm if p["member"]["source"] == "Local" and str(p["member"]["sid"]).startswith("S-1-5-21-")]
    bad_local = [p for p in local_adm if p["local_admin"]]
    unusual = [p for p in pairs.values() if p["unusual"] or p["local_admin"]]
    unusual.sort(key=lambda p: (not p["local_admin"], p["group"] != akey, len(p["hosts"]), p["member"]["name"].lower()))
    rare_count = sum(r["rare"] for g in model["prevalence"] for r in g["members"])
    boxes = [{"label": "Servers read", "value": "%d of %d" % (len(read), model["total"]), "status": "warning" if unread else "ok",
              "note": "the groups' members read"},
             {"label": "In Administrators", "value": len(adm), "note": "members, over all servers"},
             {"label": "Local accounts in Administrators", "value": len(local_adm), "status": "warning" if bad_local else "ok",
              "note": ("%d enabled, not built-in" % len(bad_local)) if bad_local else "none enabled but the built-in"},
             {"label": "Unusual members", "value": rare_count, "status": "low" if rare_count else "ok",
              "note": "on <= %s %% of answering servers" % model["rare_pct"]},
             {"label": "Servers not read", "value": len(unread), "status": "warning" if unread else "ok", "note": "see Not read"}]
    head = []
    if bad_local:
        head.append("%d enabled local account%s in Administrators" % (len(bad_local), "" if len(bad_local) == 1 else "s"))
    nu = len([p for p in pairs.values() if p["unusual"]])
    if rare_count or nu:
        head.append("unusual members found")
    if unread:
        head.append("%d server%s not read" % (len(unread), "" if len(unread) == 1 else "s"))
    top = {"title": "At a glance", "top": True, "start_here": [], "summary_only": True, "boxes": boxes,
           "headline": ", ".join(head) if head else "%d server%s read, nothing unusual" % (len(read), "" if len(read) == 1 else "s"),
           "headline_status": "warning" if bad_local or unread else ("low" if rare_count or nu else "ok")}
    if unusual:
        top.update({"status": "warning" if bad_local else "low",
                    "text": "Worst first: an enabled local account in Administrators, then members found on few of the servers.",
                    "columns": ["Member", "Group", "Kind", "Servers", "On"], "kinds": ["wrap", "", "", "num", "wrap"],
                    "rows": [[p["member"]["name"], p["label"], _kind(p["member"]), "%d of %d" % (len(p["hosts"]), p["of"]),
                              ", ".join(_shown(model, h) for h in sorted(p["admin_hosts"] if p["local_admin"] else p["hosts"]))] for p in unusual],
                    "row_status": ["warning" if p["local_admin"] else "low" for p in unusual],
                    "row_tags": [_tag([p["group"]], [p["member"]["name"]]) for p in unusual]})
    baseline = win_local_groups_baseline(model, "")["groups"]
    coverage = "Prevalence is out of %d that answered" % len(read)
    coverage += "; %d did not answer" % len(unread)
    if unread:
        coverage += ": " + ", ".join(sorted(_shown(model, u["host"]) for u in unread))
    members = sum(map(len, baseline.values()))
    top["lines"] = ["Suggested baseline: %d member%s in %d group%s (>= %s %%)" %
                    (members, "" if members == 1 else "s", len(baseline), "" if len(baseline) == 1 else "s",
                     model["baseline_pct"]), coverage]
    secs = [top]
    common, rare_rows, common_tags, rare_tags = [], [], [], []
    for group in model["prevalence"]:
        for r in group["members"]:
            row = [group["label"], r["member"], "in %d of %d servers (%d %%)" % (r["count"], len(read), r["pct"])]
            tag = _tag([group["key"]], [r["member"]])
            common.append(row)
            common_tags.append(tag)
            if r["rare"]:
                rare_rows.append(row + [", ".join(_shown(model, h) for h in r["hosts"])])
                rare_tags.append(tag)
    if rare_rows:
        secs.append({"title": "Unusual members", "columns": ["Group", "Member", "Prevalence", "On"],
                     "rows": rare_rows, "row_tags": rare_tags})
    if common:
        secs.append({"title": "Most common members", "columns": ["Group", "Member", "Prevalence"],
                     "rows": common, "row_tags": common_tags})
    # By server: one row per server, group and member (the Group and Member filters hide rows)
    rows, tags, rstat = [], [], []
    for s in sorted(read, key=lambda x: x["host"]):
        for g in s["groups"]:
            if not g["found"]:
                rows.append([_shown(model, s["host"]), g["label"], "-", "", "", "not read: " + g["error"] if g["error"] != "no such local group on this server" else "no such group here"])
                tags.append(_tag([g["key"]], []))
                rstat.append("")
                continue
            if not g["members"]:
                rows.append([_shown(model, s["host"]), g["label"], "(empty)", "", "", ""])
                tags.append(_tag([g["key"]], []))
                rstat.append("")
            for m in g["members"]:
                p = pairs[_pk(m, g)]
                note = _note(p, m)
                if g["method"] == "ADSI" and m is g["members"][0]:
                    note = (note + "; " if note else "") + "read with ADSI"
                if g.get("partial") and m is g["members"][0]:
                    note = (note + "; " if note else "") + "partial read: " + (g["error"] or "the read stopped part way")
                rows.append([_shown(model, s["host"]), g["label"], m["name"], _kind(m), m["last_logon"], note])
                tags.append(_tag([g["key"]], [m["name"]], _sel(s, p, m)))
                rstat.append("warning" if _local_admin(m, g["key"]) else ("low" if p["unusual"] else ""))
    cut = None if web else 40
    by_server = {"title": "By server", "text": "Every member of every group read, server by server.",
                 "columns": ["Server", "Group", "Member", "Kind", "Last logon", "Note"], "kinds": ["wrap", "", "wrap", "", "nw", "wrap"],
                 "rows": rows[:cut], "row_status": rstat[:cut]}
    if web:
        by_server["row_tags"] = tags
        by_server["select_export"] = dict(SELECT_BASE, check=check_parts(rules))
    elif len(rows) > 40:
        by_server["text"] = "The first 40 rows: every row is on the report website and in the workbook (%d)." % len(rows)
    if rows:
        secs.append(by_server)
    # By member: who is in which group where
    plist = sorted(pairs.values(), key=lambda p: (p["group"] != akey, p["member"]["name"].lower(), p["label"]))
    mrows = [[p["member"]["name"], p["label"], _kind(p["member"]), "%d of %d" % (len(p["hosts"]), p["of"]),
              ", ".join(_shown(model, h) for h in sorted(p["hosts"]))] for p in plist]
    by_member = {"title": "By member", "text": "Each member and group: on how many of the servers that have the group, and which.",
                 "columns": ["Member", "Group", "Kind", "Servers", "Which servers"], "kinds": ["wrap", "", "", "num", "wrap"],
                 "rows": mrows[:cut], "row_status": ["warning" if p["local_admin"] else ("low" if p["unusual"] else "") for p in plist][:cut]}
    if web:
        by_member["select_export"] = dict(SELECT_BASE, check=check_parts(rules))
        by_member["row_tags"] = [_tag([p["group"]], [p["member"]["name"]], _sel("", p)) for p in plist]
    elif len(mrows) > 40:
        by_member["text"] = "The first 40 rows: every row is on the report website and in the workbook (%d)." % len(mrows)
    if mrows:
        secs.append(by_member)
    if unread:
        secs.append({"title": "Not read", "status": "warning", "columns": ["Server", "Why"], "kinds": ["wrap", "wrap"],
                     "rows": [[u["host"], u["why"]] for u in sorted(unread, key=lambda u: u["host"])]})
    return secs


def win_local_groups_facets(model):
    """The page's filters: a button per group (with its number of members), All first, and a member search."""
    n = {}
    for p in model["pairs"].values():
        n[p["group"]] = n.get(p["group"], 0) + len(p["hosts"])
    return {"pf_label": "Group", "h_label": "Member", "h_placeholder": "A member's name, or part of it", "host": True,
            "platforms": [{"key": g["key"], "name": "%s %d" % (g["label"], n.get(g["key"], 0)), "hue": i % 8}
                          for i, g in enumerate(model["groups"])],
            "note": "The buttons and the search choose the rows of By server and By member. The tiles count all groups."}


def _sids(p):
    """The member's SID; a local account has one per server (SRV01: S-1-5-21-..., SRV02: ...)."""
    sids = p.get("sids") or {}
    if len(set(sids.values())) <= 1:
        return p["member"]["sid"]
    return "; ".join("%s: %s" % (h, sids[h]) for h in sorted(sids))


def win_local_groups_rows(model):
    """The full read, one row per server, group and member (the JSON export, the CSV, set_stats): server, computer, group,
    member, name (as Windows gives it), sid, type, source, enabled, last_logon, servers (how many have this member in this
    group). Every member, nothing left out. A group read empty or not read is a row with member ''."""
    out = []
    for s in sorted(model["read"], key=lambda x: x["host"]):
        for g in s["groups"]:
            base = {"server": s["host"], "computer": s["computer"], "group": g["label"], "group_name": g["local_name"]}
            if not g["found"] or not g["members"]:
                out.append(dict(base, member="", name="", sid="", type="", source="", enabled="", last_logon="", servers="",
                                note=("not read: " + g["error"]) if not g["found"] else "empty"))
                continue
            for m in g["members"]:
                pr = model["pairs"].get(_pk(m, g)) or {}
                out.append(dict(base, member=m["name"], name=m["raw"], sid=m["sid"], type=m["type"], source=m["source"],
                                enabled="" if m.get("enabled") is None else ("yes" if m["enabled"] else "no"),
                                last_logon=m["last_logon"], servers=len(pr.get("hosts") or []),
                                note=("partial read: " + (g["error"] or "the read stopped part way")) if g.get("partial") else ""))
    return out


CSV_COLUMNS = ["server", "group", "member", "type", "source", "sid", "enabled", "servers", "last_logon", "name", "group_name", "note"]


def csv_cell(v):
    """A CSV cell Excel never runs as a formula: text starting with = + - @ (or a tab or a return) gets a ' in front.
    Numbers stay numbers; the JSON keeps the values as they are."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def win_local_groups_csv(model):
    """By server as CSV text (the email's and the website's file)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([csv_cell(c) for c in CSV_COLUMNS])
    for row in win_local_groups_rows(model):
        w.writerow([csv_cell(row.get(c, "")) for c in CSV_COLUMNS])
    return buf.getvalue()


def win_local_groups_json(model):
    """The JSON file: every row of the read, and the servers not read."""
    return json.dumps({"kind": "win_local_groups", "not_read": model["unread"], "rows": win_local_groups_rows(model)}, indent=1, sort_keys=True)


def _approved_rows(model):
    """{group: [{entry, approve, note}]}: each (member, group) pair as an approved-list entry. Nothing is approved here: the
    person decides every row (the YAML has each with # in front, the workbook's Approve column is blank)."""
    out = dict((g["label"], []) for g in model["groups"])
    seen = set()
    for p in sorted(model["pairs"].values(), key=lambda p: (-len(p["hosts"]), p["member"]["name"].lower())):
        raw = _entry(p)
        e = literal_entry(raw, p["member"]["sid"] if len(set((p.get("sids") or {}).values())) <= 1 else "")
        if not e:
            out.setdefault(p["label"], []).append({"entry": "(left out: %s has a template mark; approve it by its SID)" % raw.replace("'", ""),
                                                   "approve": False, "note": "", "kind": _kind(p["member"]), "skip": True})
            continue
        if (p["label"], e.lower()) in seen:
            continue
        seen.add((p["label"], e.lower()))
        out.setdefault(p["label"], []).append({"entry": e, "approve": False,
                                               "note": "%d of %d servers" % (len(p["hosts"]), p["of"]), "kind": _kind(p["member"])})
    return out


def approved_kind(approved, kind="groups"):
    """The setting -> {item: [entries]} of one kind: the per-kind shape (groups: {...}) or the kind's dict alone."""
    a = approved if isinstance(approved, dict) else {}
    if isinstance(a.get(kind), dict):
        a = a[kind]
    return dict((str(k), [str(e) for e in (v or [])] if isinstance(v, (list, tuple)) else ([str(v)] if v else []))
                for k, v in a.items() if not isinstance(v, dict))


def approved_yaml(setting, kinds, header=None):
    """The approved list as YAML text to paste: setting: {kind: {item: [entries]}}. kinds: {kind: {item: [{entry, approve,
    note}]}} - an entry not approved is there as a comment, to approve by taking the # away. Names in single quotes."""
    q = lambda s: "'%s'" % str(s).replace("'", "''")
    out = ["# %s" % h for h in header or []]
    out.append("%s:" % setting)
    for kind, items in kinds.items():
        out.append("  %s:" % kind)
        for item, rows in items.items():
            yes = [x for x in rows if x.get("approve")]
            no = [x for x in rows if not x.get("approve")]
            if not yes and not no:
                out.append("    %s: []" % q(item))
            elif not yes:
                out.append("    %s:    # nothing approved yet: take the # away from each approved member" % q(item))
            else:
                out.append("    %s:" % q(item))
            for x in yes:
                out.append("      - %s%s" % (q(x["entry"]), ("    # " + x["note"]) if x.get("note") else ""))
            for x in no:
                out.append("      # - %s%s" % (q(x["entry"]), ("    # " + x["note"]) if x.get("note") else ""))
    return "\n".join(out) + "\n"


def win_local_groups_approved_yaml(model, when=""):
    """approved-list.yml: the block to paste into all.yml (or the template's Variables). Nothing approved: every member is
    there with # in front - take it away from each one that is approved."""
    head = ["The members of this read%s: %d server(s). Take the # away from each approved member, then paste it into all.yml"
            % ((" of " + when) if when else "", len(model["read"])),
            "(or the template's Variables). The job then writes the checks from it: answer-file-*.xml (Evaluate-STIG) and",
            "check-local-admins.ps1. {computer} is each server's own name. More for some servers: win_local_groups_approved_by:",
            "  [{match: 'SRV-SQL*', groups: {Administrators: ['CORP\\SQL-Admins']}}]   (match: a name glob; group: an inventory group)"]
    return approved_yaml("win_local_groups_approved", {"groups": _approved_rows(model)}, head)


# ---- the checks made from the approved list: an Evaluate-STIG answer file and a PowerShell script -----------------------
# The same text from the job (win_local_groups_approved) and from the page (Export selected, site-report.js: SelX.check):
# the fixed parts below go to the page in the section's data-sel-export, and both put only the approved entries (a CSV
# here-string: data, never code) between them. PowerShell 5.1; Constrained Language Mode reads with Get-LocalGroupMember
# only (ADSI, its fallback, needs Full Language).
CHECK_DATA_HEAD = "$approved = @'\n\"entry\",\"match\",\"hosts\""
CHECK_DATA_TAIL = "'@ | ConvertFrom-Csv"
CHECK_BODY = r"""$computer = $env:COMPUTERNAME
$domain = ''
try { $domain = [string](Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).Domain } catch { $domain = '' }
$fqdn = $computer + '.' + $domain
$cu = $computer.ToUpperInvariant()
# an entry is for this server when its match (globs on the computer name, upper case, ordinal) and its hosts (full
# names: computer.domain) say so; neither = every server
# globs and full names only for an ASCII name (the name as Windows gives it, before any case change)
$ascii = $computer -cmatch '^[\x21-\x7E]+$'
$fqdnOk = $fqdn -cmatch '^[\x21-\x7E]+$'
$want = @(@($approved) | Where-Object { $_ -and $_.entry } | Where-Object { $e = $_; (-not $e.match -or ($ascii -and @(([string]$e.match) -split ';' | Where-Object { $_ -and ($cu -clike ([string]$_).ToUpperInvariant()) }).Count -gt 0)) -and (-not $e.hosts -or ($domain -and $fqdnOk -and @(([string]$e.hosts) -split ';' | Where-Object { $_ -and $fqdn.Equals([string]$_, 'OrdinalIgnoreCase') }).Count -gt 0)) } | ForEach-Object { (([string]$_.entry) -ireplace '\{computer\}', $computer) -replace '^\.\\', ($computer + '\') })
$found = $null
try {
    $found = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop | ForEach-Object { [PSCustomObject]@{ Name = [string]$_.Name; SID = [string]$_.SID } })
} catch {
    Write-Verbose ('Get-LocalGroupMember: ' + $_)
    if ($ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') {
        try {
            $group = (New-Object System.Security.Principal.SecurityIdentifier 'S-1-5-32-544').Translate([System.Security.Principal.NTAccount]).Value -replace '^.*\\', ''
            $found = @(([ADSI]('WinNT://./' + $group + ',group')).psbase.Invoke('Members') | ForEach-Object {
                $sid = ''; try { $sid = (New-Object System.Security.Principal.SecurityIdentifier -ArgumentList ([byte[]]$_.GetType().InvokeMember('objectSid', 'GetProperty', $null, $_, $null)), 0).Value } catch { }
                $name = ''; try { $name = [string](New-Object System.Security.Principal.SecurityIdentifier $sid).Translate([System.Security.Principal.NTAccount]).Value } catch { }
                [PSCustomObject]@{ Name = $name; SID = $sid } })
        } catch { Write-Verbose ('ADSI: ' + $_); $found = $null }
    }
}
$extra = @()
$members = @()
if ($null -ne $found) {
    # ordinal, case-insensitive: an account name or SID is the same only to the character (never a linguistic match)
    $extra = @($found | Where-Object { $m = $_; -not @($want | Where-Object { $w = [string]$_; if ($w -match '^S-1-') { $w.Equals([string]$m.SID, 'OrdinalIgnoreCase') } else { $w.Equals([string]$m.Name, 'OrdinalIgnoreCase') } }).Count })
    $members = @($found | ForEach-Object { if ($_.Name -and $_.Name -ne $_.SID) { $_.Name + ' (' + $_.SID + ')' } else { 'unresolved SID ' + $_.SID } })
    $notApproved = @($extra | ForEach-Object { if ($_.Name -and $_.Name -ne $_.SID) { $_.Name + ' (' + $_.SID + ')' } else { 'unresolved SID ' + $_.SID } })
}"""
VC_TAIL = r"""if ($null -eq $found) { return @{ 'Valid' = $false; 'Results' = 'Administrators (S-1-5-32-544) could not be read.' } }
$lines = @('Members of Administrators: ' + $(if ($members.Count) { $members -join ', ' } else { '(none)' }))
if ($extra.Count -gt 0) { $lines += 'Not approved: ' + ($notApproved -join ', ') }
return @{ 'Valid' = ($extra.Count -eq 0); 'Results' = ($lines -join "`n") }"""
PS1_HEAD = r"""# check-local-admins.ps1 - site-automation 1.122.0 (Windows - local group members). Read only.
# Lists the members of Administrators (S-1-5-32-544) and the ones not on the approved list below.
# Exit 0: every member is approved. Exit 1: a member is not approved, or Administrators could not be read.
# PowerShell 5.1 or later; run it as an administrator (by hand, a GPO or SCCM)."""
PS1_TAIL = r"""if ($null -eq $found) { Write-Output 'Administrators (S-1-5-32-544) could not be read.'; exit 1 }
Write-Output ('Members of Administrators: ' + $(if ($members.Count) { $members -join ', ' } else { '(none)' }))
if ($extra.Count -gt 0) { Write-Output ('Not approved: ' + ($notApproved -join ', ')); exit 1 }
Write-Output 'Every member is approved.'
exit 0"""
VC_TRUE = "Every member of Administrators is on the site's approved list (site-automation). The members are in the results."
VC_FALSE = "A member of Administrators is not on the site's approved list (site-automation), or the group could not be read. See the results."


def check_parts(rules=None):
    """The fixed parts of the checks (for the page's data-sel-export too): the code around the data, and each answer
    file's STIG name and rules."""
    rules = rules if isinstance(rules, list) and rules else STIG_RULES
    stigs = []
    for r in rules:
        if not isinstance(r, dict) or not r.get("vuln") or any(BAD_CHARS.search(str(v)) for v in r.values()):
            continue
        # the STIG's short name: stig, else from an older override's os (2022 -> WinServer2022); neither: left out
        name = str(r.get("stig") or (("WinServer%s" % r["os"]) if re.fullmatch(r"20\d\d", str(r.get("os") or "")) else "")).strip()
        if not name:
            continue
        st = next((x for x in stigs if x["name"] == name), None)
        if st is None:
            st = {"name": name, "vulns": []}
            stigs.append(st)
        # ExpectedStatus: the status Evaluate-STIG gives the rule before the answer key runs (its own check: Not_Reviewed)
        st["vulns"].append({"vuln": str(r["vuln"]), "rule": str(r.get("rule") or ""), "role": str(r.get("role") or ""),
                            "expected": str(r.get("expected") or "Not_Reviewed")})
    p = {"data_head": CHECK_DATA_HEAD, "data_tail": CHECK_DATA_TAIL, "body": CHECK_BODY, "vc_tail": VC_TAIL,
         "ps1_head": PS1_HEAD, "ps1_tail": PS1_TAIL, "vc_true": VC_TRUE, "vc_false": VC_FALSE, "key": "DEFAULT", "stigs": stigs,
         "mark": CODE_MARK}
    # each answer file with CODE_MARK where its (escaped) code goes: the page fills it in, as answer_file does
    import xml.etree.ElementTree as ET
    good = []
    for st in stigs:
        st["file"] = "answer-file-%s.xml" % re.sub(r"[^A-Za-z0-9_.-]+", "_", st["name"] or "STIG")
        st["xml"] = _answer_xml(CODE_MARK, st, p)
        try:                         # a template that does not parse is offered by neither the job nor the page
            ET.fromstring(st["xml"].encode("utf-8"))
            good.append(st)
        except ET.ParseError:
            pass
    p["stigs"] = good
    return p


def _csv_q(v):
    return '"%s"' % str(v).replace("\r", " ").replace("\n", " ").replace('"', '""')


BAD_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\ud800-\udfff]")


def check_data(entries):
    """[(entry, match, hosts)] -> the data lines: a CSV here-string (every line starts with a double quote, so none ends it)."""
    seen, lines = set(), []
    for x in entries:
        e, m, h = (list(x) + ["", ""])[:3]
        # a character XML 1.0 does not allow (a control character) is no account name: the entry is left out
        if BAD_CHARS.search(str(e)) or BAD_CHARS.search(str(m)) or BAD_CHARS.search(str(h)):
            continue
        e = str(e).replace("\r", " ").replace("\n", " ").strip(" ")        # spaces only (the page's writer does the same)
        k = (e.lower(), str(m).lower(), str(h).lower())
        if not e or k in seen:
            continue
        seen.add(k)
        lines.append("%s,%s,%s" % (_csv_q(e), _csv_q(m), _csv_q(h)))
    return "\n".join([CHECK_DATA_HEAD] + lines + [CHECK_DATA_TAIL])


def check_code(entries, parts=None):
    """The ValidationCode: the data, the read, the compare - returns Results (the members, the ones not approved) and Valid."""
    p = parts or check_parts()
    return "\n".join([check_data(entries), p["body"], p["vc_tail"]])


def check_script(entries, parts=None):
    """check-local-admins.ps1: the same read and compare; prints, exits 0 or 1."""
    p = parts or check_parts()
    # a UTF-8 BOM: Windows PowerShell 5.1 reads a script without one in the ANSI code page (CORP\\Jose with an accent)
    return "\ufeff" + "\n".join([p["ps1_head"], check_data(entries), p["body"], p["ps1_tail"]]) + "\n"


CODE_MARK = "@@SITE-CHECK-CODE@@"


def answer_file(entries, stig, parts=None):
    """One Evaluate-STIG answer file (XML) for one STIG: <STIGComments Name>, a <Vuln ID> per rule, each its <AnswerKey>
    (written by stig_answer_filters' block writer): ExpectedStatus NotAFinding, the ValidationCode, NotAFinding when it is
    valid, Open when not."""
    p = parts or check_parts()
    return _answer_xml(check_code(entries, p), stig, p)


def _answer_xml(code, stig, p):
    head = ['<?xml version="1.0" encoding="utf-8"?>',
            "<!-- %s -->" % sa._comment("site-automation 1.122.0, Windows - local group members: the approved members of Administrators "
                                        "for %s (%s). CHECK THE NAME: set STIGComments Name to the short name your Evaluate-STIG lists for "
                                        "this STIG, and try the file on a test server first." % (
                                            stig["name"], ", ".join("%s %s" % (v["vuln"], v["rule"]) for v in stig["vulns"])))]
    body = ['<STIGComments Name="%s">' % sa._attr(stig["name"])]
    for v in stig["vulns"]:
        block = sa.stig_answer_block(p["key"], v.get("expected") or "Not_Reviewed", code, ("NotAFinding", p["vc_true"]), ("Open", p["vc_false"]))
        body += ['  <Vuln ID="%s">' % sa._attr(v["vuln"])] + sa._indent_block(block) + ["  </Vuln>"]
    body.append("</STIGComments>")
    return "\n".join(head + body) + "\n"


def _globs(v):
    """A rule's match -> its globs (on the computer name): empty ones left out, and globs with [ ] or not ASCII left out
    (the check compares upper case, ordinal: only ASCII is the same there and here). None when a selector holds a
    character XML does not allow: the rule is left out."""
    v = [v] if isinstance(v, str) else list(v or []) if isinstance(v, (list, tuple)) else []
    if any(BAD_CHARS.search(str(x)) for x in v):
        return None
    if any(";" in str(x) for x in v):
        return None                                  # ; separates the globs in the check: never inside one
    return [str(x).strip(" ") for x in v if str(x).strip(" ") and "[" not in str(x) and "]" not in str(x) and str(x).isascii()]


def _fqdn(s):
    """A server's full name as the check makes it on the server: COMPUTERNAME.domain ('' without a domain)."""
    d = str(s.get("domain") or "").strip()
    f = "%s.%s" % (s["computer"], d) if d and "." in d else ""
    # ASCII only: there the check's ordinal, case-blind compare and lower() here agree
    return f if f.isascii() and re.fullmatch(r"[\x21-\x7e]+", f) else ""


def approved_entries(model, approved, approved_by=None):
    """The Administrators entries of win_local_groups_approved as [(entry, match, hosts)] - the checks run where there is
    no inventory, so a scope is the server's own identity: match = globs on its computer name, hosts = full names
    (COMPUTERNAME.domain, as Win32_ComputerSystem says) - never a bare computer name another domain could have too.
    - The key Administrators (or S-1-5-32-544): every server.
    - A key in a server's language (Administratoren): the servers read whose S-1-5-32-544 has that name, by full name.
    - win_local_groups_approved_by: match globs as they are; group (an inventory group) as the full names of its servers
      read; both: both. A selector that is there but empty, or holds a glob left out, approves nothing."""
    read = model.get("read") or []
    local = {}
    for s in read:
        for g in s["groups"]:
            if g["sid"] == ADMINS and g["local_name"] and g["local_name"].lower() != "administrators" and _fqdn(s):
                local.setdefault(g["local_name"].lower(), set()).add(_fqdn(s))
    # a full name is kept only when every server read with it says S-1-5-32-544 is called that
    def says(s, k):
        return any(g["sid"] == ADMINS and (g["local_name"] or "").lower() == k for g in s["groups"])
    for k in list(local):
        local[k] = set(f for f in local[k] if all(says(s, k) for s in read if _fqdn(s).lower() == f.lower()))
    canon = ("administrators", ADMINS.lower())

    def scope_of(key):
        """'' = every server; a set of full names; None = no server."""
        k = str(key).lower()
        if k in canon:
            return ""
        return local.get(k) or None

    out = []
    for k, v in approved_kind(approved).items():
        sc = scope_of(k)
        if sc is not None:
            out += [(e, "", ";".join(sorted(sc)) if sc else "") for e in v]
    for rule in approved_by or []:
        if not isinstance(rule, dict):
            continue
        items = rule.get("groups") if isinstance(rule.get("groups"), dict) else dict((k, v) for k, v in rule.items() if k not in ("match", "group"))
        has_match, has_group = "match" in rule, "group" in rule
        globs = _globs(rule.get("match"))
        grp = rule.get("group")
        grp = [grp] if isinstance(grp, str) else [str(x) for x in grp or []] if isinstance(grp, (list, tuple)) else []
        if globs is None or any(BAD_CHARS.search(str(x)) for x in grp):
            continue
        grp = [x.strip().lower() for x in grp if str(x).strip()]
        if (has_match and not globs) or (has_group and not grp) or not (has_match or has_group):
            continue
        hosts = None
        if grp:
            inside = [s for s in read if _fqdn(s) and set(x.lower() for x in s.get("inv_groups") or []) & set(grp)]
            outside = set(_fqdn(s).lower() for s in read if s not in inside)
            hosts = set(_fqdn(s) for s in inside if _fqdn(s).lower() not in outside)
            if not hosts:
                continue
        for k, v in approved_kind(items).items():          # 1.122.2: one name (not a list) is one entry, never its letters
            sc = scope_of(k)
            if sc is None:
                continue
            h = hosts if sc == "" else (sc if hosts is None else sc & hosts)
            if h is not None and not h:
                continue
            out += [(str(e), ";".join(globs) if has_match else "", ";".join(sorted(h)) if h else "") for e in v]
    return out


def win_local_groups_checks(model, approved, approved_by=None, rules=None):
    """The files made from the approved list: [{name, content}] - an answer file per STIG and check-local-admins.ps1;
    [] when the list names no Administrators member."""
    ents = approved_entries(model, approved, approved_by)
    if not ents:
        return []
    p = check_parts(rules)
    import xml.etree.ElementTree as ET
    files = []
    for st in p["stigs"]:
        text = answer_file(ents, st, p)
        try:                                                 # only a file that parses is offered (as stig_answer_xml_files)
            ET.fromstring(text.encode("utf-8"))
        except ET.ParseError:
            continue
        files.append({"name": st["file"], "content": text})
    return (files + [{"name": "check-local-admins.ps1", "content": check_script(ents, p)}])


def win_local_groups_sheets(model):
    """The workbook: Approved list (an Approve column, blank: the person decides), By member, By server (each with a Group
    column, for Excel's filter), Not read."""
    pairs = model["pairs"]
    bm = [[p["member"]["name"], p["label"], _kind(p["member"]), _sids(p), len(p["hosts"]), p["of"],
           "yes" if p["unusual"] else "", ", ".join(sorted(p["hosts"]))]
          for p in sorted(pairs.values(), key=lambda p: (p["member"]["name"].lower(), p["label"]))]
    bs = []
    for row in win_local_groups_rows(model):
        m = _find_member(model, row) if row["member"] else None
        p = pairs.get("%s|%s" % (_gkey_of(model, row["group"]), m["key"])) if m else None
        note = "; ".join(x for x in (row["note"], _note(p, m) if p else "") if x)          # THIS server's account (enabled or not)
        bs.append([row["server"], row["group"], row["member"], row["type"], row["source"], row["sid"], row["enabled"], row["servers"],
                   _kind(m) if m else "", row["last_logon"], row["group_name"], note])
    sheets = [{"name": "By member", "columns": ["Member", "Group", "Kind", "SID", "Servers", "Of", "Unusual", "Which servers"], "rows": bm},
            {"name": "By server", "columns": ["Server", "Group", "Member", "Type", "Source", "SID", "Enabled", "Servers with this member",
                                              "Kind", "Last logon", "Name on the server", "Note"],
             "rows": bs},
            {"name": "Not read", "columns": ["Server", "Why"], "rows": [[u["host"], u["why"]] for u in model["unread"]]}]
    ap = _approved_rows(model)
    sheets.insert(0, {"name": "Approved list", "columns": ["Group", "Entry", "Approve", "Servers", "Kind"],
                      "rows": [[g, x["entry"], "yes" if x["approve"] else "", x["note"], x["kind"]] for g in ap for x in ap[g]]
                      + [[g, "(no member on any server)", "", "", ""] for g in ap if not ap[g]]})
    baseline = win_local_groups_baseline(model, "")
    rows = [[group, r["member"], r["count"], baseline["servers_answered"], r["pct"], baseline["threshold_pct"]]
            for group, members in baseline["groups"].items() for r in members]
    columns = ["Group", "Member", "Servers", "Servers answered", "Percent", "Threshold (%)",
               "Administrators (one-line YAML)", "Administrators (block YAML)"]
    if rows:
        note, flow, block = _baseline_yaml(model)
        # Excel truncates text after 32,767 UTF-16 units. Keep oversized snippets usable via the attachment.
        for snippet in (flow, block):
            snippet = BAD_CHARS.sub("", snippet)
            rows[0].append(snippet if len(snippet.encode("utf-16-le")) <= 65534 else "See suggested-baseline.txt")
        if note:
            columns.append("Warning")
            rows[0].append(note.strip().lstrip("# "))
    sheets.append({"name": "Suggested baseline", "columns": columns, "rows": rows})
    return sheets


def _gkey_of(model, label):
    return next((g["key"] for g in model["groups"] if g["label"] == label), "")


def _find_member(model, row):
    s = next((x for x in model["read"] if x["host"] == row["server"]), None)
    g = next((x for x in (s or {}).get("groups", []) if x["label"] == row["group"]), None)
    return next((m for m in (g or {}).get("members", []) if m["sid"] == row["sid"] and m["raw"] == row["name"]), None)


class FilterModule(object):
    def filters(self):
        return {"win_local_groups_baseline": win_local_groups_baseline, "win_local_groups_baseline_snippet": win_local_groups_baseline_snippet,
                "win_local_groups_parse": win_local_groups_parse, "win_local_groups_model": win_local_groups_model,
                "win_local_groups_findings": win_local_groups_findings, "win_local_groups_sections": win_local_groups_sections,
                "win_local_groups_facets": win_local_groups_facets, "win_local_groups_sheets": win_local_groups_sheets,
                "win_local_groups_rows": win_local_groups_rows, "win_local_groups_csv": win_local_groups_csv,
                "win_local_groups_json": win_local_groups_json, "win_local_groups_approved_yaml": win_local_groups_approved_yaml,
                "win_local_groups_checks": win_local_groups_checks}
