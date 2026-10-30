# -*- coding: utf-8 -*-
"""The report of playbooks/fapolicyd_denials.yml: every host's fapolicyd denials and ACT's analysis of them,
from what ACT's playbook (vendor/act/ansible/playbooks/fapolicyd_denials.yml) left in each host's variables -
_fa_rpm, _fa_active, _fa_auditd, _fa_count, _fa_paths, _fa_exes, _fa_findings and act_triage_result (only where
ACT ran: a host with denials or a problem). One report (roles/site_email's _report) for the whole job.
And playbooks/fapolicyd.yml's (roles/fapolicyd and its two parts, roles/fapolicyd_fix and roles/fapolicyd_rules):
getting fapolicyd running, its blocks, trust entries and rules, the rules files' clean-up - the plans, the way back,
the workflow's plan, one report."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
from shlex import quote

ORDER = {"critical": 0, "warning": 1, "ok": 2}
NOTE = ("ACT's proposals (a trust entry, a narrow rule) are security-control changes: route them through your "
        "security approval (e.g. the ISSO) before anyone applies them.")


def _get(hv, key, default=None):
    try:
        v = hv[key] if key in hv else default
    except Exception:            # a variable that does not template is as good as missing
        return default
    return default if v is None else v


def act_md_blocks(text):
    """ACT's answer (Markdown) as [(heading, [lines])]: a block per heading (the text before the first one: heading ""),
    a line per paragraph, list item and command - no **, `, # or --- left. A report section shows its lines as a list;
    the answer as one paragraph ran its headings, lists and commands together."""
    blocks, lines, heading, code, para = [], [], "", False, False
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        s = raw.strip()
        if s.startswith("```"):
            code, para = not code, False
            continue
        if code:
            if s:
                lines.append(s)
            continue
        if not s:
            para = False
            continue
        if re.match(r"^([-*_])(\s*\1){2,}$", s):                   # a rule: ---
            para = False
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            if lines or heading:
                blocks.append((heading, lines))
            heading, lines, para = _md_plain(m.group(1)).rstrip(":"), [], False
            continue
        m = re.match(r"^(?:[-*+\u2022]\s+|(\d{1,2})[.)]\s+)(.*)$", s)
        if m:
            lines.append(((m.group(1) + ". ") if m.group(1) else "") + _md_plain(m.group(2)))
            para = True
            continue
        if para and lines:
            lines[-1] = lines[-1] + " " + _md_plain(s)
        else:
            lines.append(_md_plain(s))
            para = True
    if lines or heading:
        blocks.append((heading, lines))
    return [(h, [x for x in ls if x]) for h, ls in blocks if h or [x for x in ls if x]]


def _md_plain(s):
    return " ".join(re.sub(r"\*\*|__|`+", "", str(s)).split())


def _out(reg):
    return str(_get(reg, "stdout", "") if hasattr(reg, "__contains__") else "").strip()


def host_result(name, hv):
    """One host -> {host, state: 'not read' | 'not installed' | 'read', status, ...}."""
    rpm = _get(hv, "_fa_rpm")
    if not hasattr(rpm, "__contains__") or "rc" not in rpm:
        return {"host": name, "state": "not read", "status": "warning"}
    if rpm["rc"] != 0:
        return {"host": name, "state": "not installed", "status": "ok"}
    active, auditd = _out(_get(hv, "_fa_active", {})) or "not running", _out(_get(hv, "_fa_auditd", {})) or "not running"
    try:
        count = int(_get(hv, "_fa_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    status = "critical" if active != "active" else ("warning" if count or auditd != "active" else "ok")
    act = _get(hv, "act_triage_result")
    act = dict(act) if hasattr(act, "keys") else None
    return {"host": name, "state": "read", "status": status, "active": active, "auditd": auditd, "count": count,
            "paths": list(_get(hv, "_fa_paths", []) or []), "exes": list(_get(hv, "_fa_exes", []) or []),
            "findings": list(_get(hv, "_fa_findings", []) or []), "act": act}


def act_sections(host, a, findings=None, status=None):
    """ACT's analysis of one host as report sections: what the job found, then ACT's answer by its own headings (the part
    before the first heading in the first section, each heading a section of its own), then ACT's proposals."""
    lines, more = list(findings or []), []
    if a is None:
        text = "ACT did not run on this host (a Check run, or the host failed before it): the findings only."
    elif str(a.get("status")) == "error":
        text = "ACT could not analyse it: %s" % (a.get("stop_reason") or a.get("summary") or "no reason given")
    else:
        blocks = act_md_blocks(a.get("summary") or a.get("stop_reason") or "") or [("", ["(ACT gave no summary)"])]
        text = "What the job found, then ACT's analysis - it read the evidence only and ran nothing on the host."
        if not blocks[0][0]:
            lines += blocks.pop(0)[1]
        more = [{"title": "%s: %s" % (host, b[0]), "lines": b[1] or ["-"]} for b in blocks]
    props = ["ACT proposes (for your security approval): %s" % d["command"]
             for d in (a or {}).get("denied") or [] if isinstance(d, dict) and d.get("command")]
    first = {"title": "%s: ACT's analysis" % host, "text": text, "lines": lines + props}
    if status:
        first["status"] = status
    return [first] + more


def fapolicyd_report(hostvars_list, names, since="today", title=""):
    """[hostvars of each host], [their names], the ausearch -ts keyword, the report title (site_report_title)
    -> roles/site_email's _report."""
    hosts = [host_result(n, hv) for n, hv in zip(names, hostvars_list)]
    read = [h for h in hosts if h["state"] == "read"]
    unread = [h["host"] for h in hosts if h["state"] == "not read"]
    missing = [h["host"] for h in hosts if h["state"] == "not installed"]
    problem = sorted([h for h in read if h["status"] != "ok"], key=lambda h: (ORDER[h["status"]], -h["count"], h["host"]))
    quiet = [h["host"] for h in read if h["status"] == "ok"]
    denied = [h for h in read if h["count"]]
    down = [h for h in read if h["active"] != "active"]
    total = sum(h["count"] for h in read)

    status = "critical" if down else ("warning" if problem or unread else "ok")
    head = []
    if denied:
        head.append("%d host(s) with denials" % len(denied))
    if down:
        head.append("fapolicyd not running on %d" % len(down))
    if [h for h in read if h["auditd"] != "active"]:
        head.append("auditd not running on %d" % len([h for h in read if h["auditd"] != "active"]))
    report = {
        "title": "%s: %s" % (title or "fapolicyd denials", ", ".join(head) if head else "no denials since %s" % since),
        "subtitle": "%d host(s) read - denials since %s (the audit log: ausearch -m FANOTIFY -ts %s)" % (len(read), since, since),
        "status": status,
        "summary": [{"label": "Denials", "value": total, "status": "warning" if total else "ok"},
                    {"label": "Hosts with denials", "value": len(denied), "status": "warning" if denied else "ok"},
                    {"label": "fapolicyd not running", "value": len(down), "status": "critical" if down else "ok"},
                    {"label": "Hosts read", "value": len(read)}]
                   + ([{"label": "Not read", "value": len(unread), "status": "warning"}] if unread else []),
        "sections": [],
        "footer": NOTE,
    }
    secs = report["sections"]
    if problem:
        rows, row_status, cell_status = [], [], []
        for h in problem:
            rows.append([h["host"], h["active"], h["auditd"], h["count"], ", ".join(h["paths"]) or "-", ", ".join(h["exes"]) or "-"])
            row_status.append(h["status"])
            cell_status.append(["", "ok" if h["active"] == "active" else "critical", "ok" if h["auditd"] == "active" else "warning",
                                "warning" if h["count"] else "", "", ""])
        secs.append({"title": "Hosts with denials or a problem", "status": status if status != "ok" else "warning",
                     "text": "What fapolicyd blocked since %s: the files denied and the programs that tried. A denial is "
                             "fapolicyd doing its job, or a legitimate program missing from the trust database - ACT's "
                             "analysis below says which. auditd not running = denials are not recorded." % since,
                     "columns": ["Host", "fapolicyd", "auditd", "Denials", "Files denied", "Programs"],
                     "rows": rows, "row_status": row_status, "cell_status": cell_status})
        for h in problem:
            secs.extend(act_sections(h["host"], h["act"], h["findings"], h["status"]))
    if quiet:
        secs.append({"title": "No denials", "status": "ok", "lines": ["%d host(s): fapolicyd running, auditd running, nothing "
                                                                        "denied since %s - %s" % (len(quiet), since, ", ".join(sorted(quiet)))]})
    if missing:
        secs.append({"title": "fapolicyd not installed", "lines": ["%d host(s), not looked at: %s" % (len(missing), ", ".join(sorted(missing)))]})
    if unread:
        secs.append({"title": "Not read", "status": "warning",
                     "lines": ["%d host(s) could not be reached or failed before their denials were read: %s"
                               % (len(unread), ", ".join(sorted(unread)))]})
    return report


# ---- getting fapolicyd running (a part of playbooks/fapolicyd.yml: roles/fapolicyd_fix) ----------------------------
FNOTE = ("Each fix changes how fapolicyd, a security control, runs: approve it through your security approval (e.g. the "
         "ISSO) before it is listed in fapolicyd_approved. A host where fapolicyd was running and is not after a fix "
         "gets everything put back and fapolicyd restarted.")
FIX_KNOWN = re.compile(r"^(trust file access: |SELinux label: |restart fapolicyd \(it is |db_max_size: |permissive: |enable fapolicyd at boot )")
FIX_DB = re.compile(r"^db_max_size:\s*(\S+)\s*->\s*(\S+)$")
# each line exactly as the report gives it - a line copied with anything more (a host name after it) is refused
FIX_FORM = (re.compile(r"^trust file access: /\S+ \S+:\S+ [0-7]{4} -> \S+:\S+ [0-7]{4}$"),
            re.compile(r"^SELinux label: /\S+ \S+:\S+ -> \S+:\S+$"),
            re.compile(r"^restart fapolicyd \(it is [a-z-]+\)$"),
            re.compile(r"^permissive: [01] -> [01]$"),
            re.compile(r"^enable fapolicyd at boot \(it is [a-z-]+\)$"),
            FIX_DB)
FIX_ORDER = {"access": 0, "label": 1, "db": 2, "enable": 3, "permissive": 4, "restart": 5}


def _norm(s):
    return " ".join(str(s or "").split())


def fapolicyd_fix_plan(ff, approved):
    """What to change on one host: the read module's result (_ff) and the approved lines (fapolicyd_fix_approved) ->
    {apply: [fixes], db_to, bad: [{line, why}] (a line this job never gives: nothing changes on any host), refused:
    [{line, why}] (approved, not possible on this host), not_here: [lines] (not proposed on this host now), restart,
    update (fapolicyd-cli --update: trust files changed while it runs), changed}. A line is applied only when this run
    proposes it again - the state it was approved for; a db_max_size line: its "from" must be this host's, its "to" may
    be the approver's own number (or auto, where this fapolicyd sizes its database itself)."""
    ff = ff or {}
    want = []
    for a in approved or []:
        a = _norm(a)
        if a and a not in want:
            want.append(a)
    bad, refused, apply, used = [], [], [], set()
    for a in want:
        m = FIX_DB.match(a)
        if not FIX_KNOWN.match(a):
            bad.append({"line": a, "why": "not a line this job gives (trust file access, SELinux label, db_max_size, enable at boot, permissive, restart fapolicyd)"})
        elif a.startswith("db_max_size:") and not m:
            bad.append({"line": a, "why": "a db_max_size line is db_max_size: <now> -> <MB or auto>"})
        elif not any(f.match(a) for f in FIX_FORM):
            bad.append({"line": a, "why": "not exactly as the report gives it (copied with a host name or more after it?)"})
        elif m and not (m.group(2) == "auto" or (m.group(2).isdigit() and 1 <= int(m.group(2)) <= 1048576)):
            bad.append({"line": a, "why": "db_max_size takes a number of MB (1 to 1048576) or auto"})
    badl = set(b["line"] for b in bad)
    for f in sorted(ff.get("fixes") or [], key=lambda f: FIX_ORDER.get(f.get("kind"), 9)):
        if f.get("kind") == "db":
            for a in want:
                m = FIX_DB.match(a)
                if a in badl or not m or m.group(1) != f.get("from"):
                    continue
                used.add(a)
                if m.group(2) == "auto" and not f.get("auto"):
                    refused.append({"line": a, "why": "this fapolicyd (%s) does not size its database itself: give a number" % ff.get("version", "?")})
                else:
                    apply.append(dict(f, to=m.group(2), proposed=f.get("line"), line="db_max_size: %s -> %s" % (f.get("from"), m.group(2))))
                break
        elif _norm(f.get("line")) in want:
            apply.append(f)
            used.add(_norm(f.get("line")))
    kinds = set(f["kind"] for f in apply)
    active = ff.get("active") == "active"
    restart = "restart" in kinds or (bool(kinds & set(["db", "permissive"])) and active)
    return {"apply": apply, "db_to": ([f["to"] for f in apply if f["kind"] == "db"] + [""])[0],
            "permissive_to": ([f["to"] for f in apply if f["kind"] == "permissive"] + [""])[0], "enable": "enable" in kinds,
            "bad": bad, "refused": refused,
            "not_here": [a for a in want if a not in used and a not in badl], "restart": restart,
            "update": active and not restart and bool(kinds & set(["access", "label"])), "changed": bool(apply)}


def fapolicyd_fix_rollback(apply, conf="/etc/fapolicyd/fapolicyd.conf", backup=""):
    """The shell function back() that puts back what the fixes change (owner, group, mode, SELinux label,
    fapolicyd.conf), the last change first, and fails when any of it does not go back. Sent inline with the restart
    (fapolicyd_fix_reload.sh) - never a file run on the host while fapolicyd runs."""
    out = ["back() {", "    rc=0"]
    for f in reversed(list(apply or [])):
        if f.get("kind") == "access":
            og, mode = (f.get("from") or "").rsplit(" ", 1)
            out.append("    chown -h %s %s || rc=1" % (quote(og), quote(f["path"])))
            out.append("    chmod %s %s || rc=1" % (quote(mode), quote(f["path"])))
        elif f.get("kind") == "label":
            out.append("    chcon -h %s %s || rc=1" % (quote(f["from"]), quote(f["path"])))
        elif f.get("kind") == "enable":
            out.append("    systemctl disable fapolicyd >/dev/null 2>&1 || rc=1")
        elif f.get("kind") in ("db", "permissive") and backup and not any("cp -p" in x for x in out):
            out.append("    cp -p %s %s || rc=1" % (quote(backup), quote(conf)))
    return "\n".join(out + ["    return $rc", "}"]) + "\n"


def _fix_host(name, hv):
    ff = _get(hv, "_ff")
    if not hasattr(ff, "keys") or "state" not in ff:
        return {"host": name, "state": "not read", "error": "", "fixes": [], "by_hand": [], "journal": [], "db": {}}
    h = dict(ff)
    h["host"] = name
    ap = _get(hv, "_ff_apply")
    h["apply"] = dict(ap) if hasattr(ap, "keys") else None
    return h


def fapolicyd_fix_report(hostvars_list, names, title="", check=False):
    """[hostvars of each host], [their names], the report title, a Check run -> roles/site_email's _report: what keeps
    fapolicyd down or near its limits, the fixes to approve, what this run fixed, what only a person can judge."""
    hosts = [_fix_host(n, hv) for n, hv in zip(names, hostvars_list)]
    read = [h for h in hosts if h["state"] == "read" and not h.get("error")]
    unread = [h for h in hosts if h["state"] == "not read" or (h["state"] == "read" and h.get("error"))]
    missing = [h["host"] for h in hosts if h["state"] == "not installed"]
    applied = [h for h in hosts if h.get("apply")]
    bad_states = ("failed", "rolled back", "refused", "still down")
    failed = [h for h in applied if h["apply"].get("state") in bad_states]
    fixed = [h for h in applied if h["apply"].get("state") in ("changed", "would change")]
    done = {}
    for h in fixed:
        done[h["host"]] = set(_norm(f.get("proposed") or f.get("line")) for f in h["apply"].get("applied") or [])
    down = [h for h in read if (h["apply"] or {}).get("active_after", h.get("active")) != "active"]
    proposals = {}
    for h in read:
        for f in h.get("fixes") or []:
            if _norm(f.get("line")) not in done.get(h["host"], set()):
                proposals.setdefault(f["line"], []).append(h["host"])
    by_hand = [(h["host"], b) for h in read for b in h.get("by_hand") or []]
    status = "critical" if down or failed else ("warning" if proposals or unread or by_hand else "ok")
    head = []
    if proposals:
        head.append("%d fix(es) to approve on %d host(s)" % (len(proposals), len(set(x for hs in proposals.values() for x in hs))))
    if fixed:
        head.append(("would fix %d host(s)" if check else "fixed on %d host(s)") % len(fixed))
    if failed:
        head.append("not fixed on %d host(s)" % len(failed))
    if down:
        head.append("fapolicyd not running on %d" % len(down))
    report = {
        "title": "%s: %s" % (title or "fapolicyd fix", ", ".join(head) if head else "nothing to fix"),
        "subtitle": "%d host(s) read - fapolicyd's trust files, SELinux labels, db_max_size and service%s"
                    % (len(read), " - Check: nothing changed" if check else ""),
        "status": status,
        "summary": [{"label": "Fixes to approve", "value": len(proposals), "status": "warning" if proposals else "ok"},
                    {"label": "fapolicyd not running", "value": len(down), "status": "critical" if down else "ok"}]
                   + ([{"label": "Hosts fixed" if not check else "Hosts it would fix", "value": len(fixed), "status": "ok"}] if applied else [])
                   + ([{"label": "Not fixed", "value": len(failed), "status": "critical"}] if failed else [])
                   + [{"label": "For a person", "value": len(by_hand), "status": "warning" if by_hand else "ok"},
                      {"label": "Hosts read", "value": len(read)}]
                   + ([{"label": "Not read", "value": len(unread), "status": "warning"}] if unread else []),
        "sections": [],
        "footer": FNOTE,
    }
    secs = report["sections"]
    if applied:
        rows, rs = [], []
        for h in applied:
            a = h["apply"]
            st = a.get("state", "")
            why = "; ".join([x for x in [a.get("error"), a.get("note"), a.get("back")] if x]
                            + ["%s: %s" % (b["line"], b["why"]) for b in (a.get("bad") or []) + (a.get("refused") or [])])
            rows.append([h["host"], st, "\n".join(f["line"] for f in a.get("applied") or []) or "-",
                         "%s -> %s" % (h.get("active", "?"), a.get("active_after") or h.get("active", "?")),
                         "\n".join(a.get("not_here") or []) or "-", why or "-"])
            rs.append("critical" if st in bad_states else "ok")
        secs.append({"title": "What this run fixed" if not check else "What this run would fix",
                     "status": "critical" if failed else "ok",
                     "text": "The fixes approved (fapolicyd_approved) that this run proposed again on the host - a line for "
                             "a state the host is no longer in is not applied (\"Not proposed here\"). After them: fapolicyd "
                             "restarted (approved, or db_max_size changed while it ran) or its trust reloaded (fapolicyd-cli "
                             "--update), and a program still runs (python). Running before and not after: everything put back."
                             + (" Check: nothing was changed." if check else ""),
                     "columns": ["Host", "Result", "Fixed", "fapolicyd", "Not proposed here", "Why"], "rows": rows, "row_status": rs})
    used = set()
    for h in applied:
        a = h["apply"]
        used |= set(_norm(f.get("proposed") or f.get("line")) for f in a.get("applied") or [])
        used |= set(_norm(f.get("line")) for f in a.get("applied") or [])
        used |= set(_norm(b.get("line")) for b in (a.get("refused") or []) + (a.get("bad") or []))
    idle = sorted(set(_norm(x) for h in applied for x in h["apply"].get("not_here") or []) - used)
    if idle:
        secs.append({"title": "Approved, made on no host", "status": "warning",
                     "text": "Lines in fapolicyd_approved that no host of this run has now: fixed already (a run before), for "
                             "a host outside this run's Limit, or not exactly the report's line. Nothing was changed for them.",
                     "lines": idle})
    if proposals:
        rows, rs = [], []
        for h in read:
            for f in h.get("fixes") or []:
                if f["line"] in proposals:
                    rows.append([h["host"], f["line"], f.get("why", "")])
                    rs.append("critical" if f["kind"] == "restart" else "warning")
        secs.append({"title": "Fixes to approve", "status": "warning",
                     "text": "Each line is a fix (its hosts and why: below). To apply them, after your security approval, list the "
                             "lines in fapolicyd_fix_approved (the template's Variables) exactly as here and run the job again - a Check "
                             "run first shows what it would change. A db_max_size line may end in your own number of MB. A line is "
                             "applied on every host of the run that has it (use a Limit for fewer). Trust file access: to the "
                             "owner, group and mode the fapolicyd package gives its own trust files.",
                     "lines": [line for line, _ in sorted(proposals.items(), key=lambda x: (FIX_ORDER.get(_kind(x[0]), 9), x[0]))]})
        secs.append({"title": "Why each fix", "columns": ["Host", "Fix", "Why"], "rows": rows, "row_status": rs})
    if by_hand:
        secs.append({"title": "For a person", "status": "warning",
                     "text": "What the job does not change: decide it with the system's owner and your security approval.",
                     "lines": ["%s: %s" % x for x in by_hand]})
    if read:
        rows, rs = [], []
        for h in read:
            d = h.get("db") or {}
            rows.append([h["host"], h.get("active", "?") + (" (permissive)" if h.get("permissive") else ""), h.get("enabled", "?"), h.get("version", ""),
                         "%s%s" % (d.get("current", "?"), " (auto possible)" if d.get("auto") and d.get("current") != "auto" else ""),
                         "%s MB (%s MB of trust files, %s RPM files)" % (d.get("need_mb", "?"), d.get("trust_mb", "?"), d.get("rpm_files", "?")),
                         len(h.get("fixes") or [])])
            rs.append("critical" if h.get("active") != "active" else ("warning" if h.get("fixes") else "ok"))
        secs.append({"title": "The hosts", "status": "critical" if down else ("warning" if proposals else "ok"),
                     "text": "fapolicyd on each host, db_max_size and the estimate it is weighed against (the trust files and the "
                             "RPM files, about twice that in the database), and how many fixes it has.",
                     "columns": ["Host", "fapolicyd", "At boot", "Version", "db_max_size", "Estimate", "Fixes"], "rows": rows, "row_status": rs})
    for h in read:
        j = ((h["apply"] or {}).get("journal") or []) or (h.get("journal") if h.get("active") != "active" or h.get("fixes") else [])
        if j:
            secs.append({"title": "%s: fapolicyd's journal" % h["host"], "status": "critical" if h.get("active") != "active" else "info",
                         "text": "fapolicyd's own lines this boot that say something went wrong (journalctl -u fapolicyd -b)"
                                 + (", after this run's fix" if (h["apply"] or {}).get("journal") else "") + ".",
                         "lines": [x[:400] for x in j]})
    if missing:
        secs.append({"title": "fapolicyd not installed", "lines": ["%d host(s), not looked at: %s" % (len(missing), ", ".join(sorted(missing)))]})
    if unread:
        secs.append({"title": "Not read", "status": "warning",
                     "lines": ["%s: %s" % (h["host"], h.get("error") or "could not be reached, or failed before it was read")
                               for h in sorted(unread, key=lambda h: h["host"])]})
    return report


# ---- its blocks and rules (a part of playbooks/fapolicyd.yml: roles/fapolicyd_rules) -------------------------------
RNOTE = ("Each change is to a security control: approve it through your security approval (e.g. the ISSO) before it is "
         "listed in fapolicyd_approved. A host where fapolicyd was running and is not after the change - or a "
         "program no longer starts - gets every file put back.")
RULES_FORM = (re.compile(r"^trust file: /\S+ size \d+ sha256 [0-9a-f]{64}$"),
              re.compile(r"^allow rule: allow\S* perm=\S+ .+ : .+$"),
              re.compile(r"^remove rule: [A-Za-z0-9._+-]+\.rules: \S.*$"),
              re.compile(r"^rename rules file: [A-Za-z0-9._+-]+\.rules -> [A-Za-z0-9._+-]+\.rules$"),
              re.compile(r"^permissive: [01] -> [01]$"))
RULES_ORDER = {"trust": 0, "rule": 1, "remove": 2, "rename": 3, "permissive": 4}


def fapolicyd_rules_job_plan(fr, approved, when_enforcing=False):
    """What to change on one host: the read module's result (_fr) and the approved lines (fapolicyd_rules_approved) ->
    {apply, trust: [paths], add: [rules], remove: {file: [rules]}, rename: [[from, to]], permissive_to, action (restart,
    rules, update or none), bad, refused, not_here, changed}. A line is applied only when this run proposes it again.
    A clean-up that blocks more (a rule that opened too much, taken out) is applied only while fapolicyd is permissive,
    unless when_enforcing - a program it would break shows up as a would-be block, not as an outage."""
    fr = fr or {}
    want = []
    for a in approved or []:
        a = _norm(a)
        if a and a not in want:
            want.append(a)
    bad = [{"line": a, "why": "not exactly a line this job gives (trust file, allow rule, remove rule, rename rules file, "
                              "permissive) - copied with a host name or more after it?"}
           for a in want if not any(f.match(a) for f in RULES_FORM)]
    badl = set(b["line"] for b in bad)
    permissive = bool(fr.get("permissive"))
    # enforcing in this run too: permissive: 1 -> 0 approved beside it leaves no window to see what it breaks
    to_enforce = permissive and any(_norm(c.get("line")) == "permissive: 1 -> 0" for c in fr.get("changes") or []) \
        and "permissive: 1 -> 0" in want
    apply, refused, used = [], [], set()
    for c in sorted(fr.get("changes") or [], key=lambda c: RULES_ORDER.get(c.get("kind"), 9)):
        line = _norm(c.get("line"))
        if line not in want or line in badl:
            continue
        used.add(line)
        if c.get("stricter") and not when_enforcing and (not permissive or to_enforce):
            refused.append({"line": line, "why": ("it blocks more, and permissive: 1 -> 0 is in this run: make it first, watch "
                                                  "for would-be blocks, enforce in a later run") if to_enforce else
                            "it blocks more, and fapolicyd is enforcing: approve permissive: 0 -> 1 first "
                            "(or fapolicyd_rules_when_enforcing: true)"})
            continue
        apply.append(c)
    kinds = set(c["kind"] for c in apply)
    remove = {}
    for c in apply:
        if c["kind"] == "remove":
            remove.setdefault(c["file"], []).append(c["rule"])
    if "permissive" in kinds:
        action = "restart"
    elif kinds & set(["rule", "remove", "rename"]):
        action = "rules"
    elif "trust" in kinds:
        action = "update"
    else:
        action = "none"
    return {"apply": apply, "trust": [c["path"] for c in apply if c["kind"] == "trust"],
            "trust_lines": ["%s %s %s" % (c["path"], c["size"], c["sha256"]) for c in apply if c["kind"] == "trust"],
            "add": [c["rule"] for c in apply if c["kind"] == "rule"], "remove": remove,
            "rename": [[c["file"], c["to"]] for c in apply if c["kind"] == "rename"],
            "permissive_to": ([c["to"] for c in apply if c["kind"] == "permissive"] + [""])[0], "action": action,
            "bad": bad, "refused": refused, "not_here": [a for a in want if a not in used and a not in badl], "changed": bool(apply)}


def fapolicyd_rules_job_edit(text, rules, stamp=""):
    """A rules file's text with each rule in rules commented out - its last copy in the file (a repeat is the later one),
    the line kept, marked: '# removed by site-automation (fapolicyd rules job, STAMP): <rule>'."""
    lines = str(text or "").split("\n")
    for rule in rules or []:
        want = _norm(rule)
        for i in range(len(lines) - 1, -1, -1):
            if _norm(lines[i]) == want:
                lines[i] = "# removed by site-automation (fapolicyd rules job%s): %s" % ((", " + stamp) if stamp else "", lines[i].strip())
                break
    return "\n".join(lines)


def fapolicyd_rules_job_file(managed, add, playbook="playbooks/fapolicyd.yml"):
    """The job's own rules file: the rules in it now, then the approved ones."""
    rules = list(managed or [])
    rules += [r for r in add or [] if r not in rules]
    return ("# Managed by site-automation (%s): the allow rules approved for this host.\n"
            "# Do not edit: approve them in fapolicyd_rules_approved and run the job.\n" % playbook) + "".join("%s\n" % r for r in rules)


def fapolicyd_rules_job_backups(plan, fr):
    """[[the file, its copy's name in the way-back folder]] for each file the change touches that is there now - the
    names fapolicyd_rules_job_rollback puts back from."""
    fr = fr or {}
    rules_d = (fr.get("own_rules") or "/etc/fapolicyd/rules.d/x").rsplit("/", 1)[0]
    out = []
    if plan.get("add") and fr.get("own_rules_exists"):
        out.append([fr["own_rules"], "own.rules"])
    if plan.get("trust") and fr.get("own_trust_exists"):
        out.append([fr["own_trust"], "own.trust"])
    out += [[rules_d + "/" + f, "rules." + f] for f in sorted((plan.get("remove") or {}).keys())]
    if plan.get("permissive_to"):
        out.append([rules_d.rsplit("/", 1)[0] + "/fapolicyd.conf", "fapolicyd.conf"])
    return out


def fapolicyd_rules_job_rollback(plan, fr, backup):
    """The shell function back() that puts every file the change touches as it was - from the copies in backup (made
    before anything changed), a file that did not exist taken out again - and fails when any of it does not go back."""
    fr = fr or {}
    rules_d = (fr.get("own_rules") or "/etc/fapolicyd/rules.d/x").rsplit("/", 1)[0]
    conf = rules_d.rsplit("/", 1)[0] + "/fapolicyd.conf"
    out = ["back() {", "    rc=0"]

    def put(path, existed, name):
        if existed:
            out.append("    cp -p %s %s || rc=1" % (quote(backup + "/" + name), quote(path)))
        else:
            out.append("    rm -f %s || rc=1" % quote(path))
    for a, b in reversed(plan.get("rename") or []):
        out.append("    [ -e %s ] && mv -f %s %s || true" % (quote(rules_d + "/" + b), quote(rules_d + "/" + b), quote(rules_d + "/" + a)))
    for f in sorted((plan.get("remove") or {}).keys()):
        put(rules_d + "/" + f, True, "rules." + f)
    if plan.get("add"):
        put(fr.get("own_rules"), fr.get("own_rules_exists"), "own.rules")
    if plan.get("trust"):
        put(fr.get("own_trust"), fr.get("own_trust_exists"), "own.trust")
    if plan.get("permissive_to"):
        put(conf, True, "fapolicyd.conf")
    return "\n".join(out + ["    return $rc", "}"]) + "\n"


def _rules_host(name, hv):
    fr = _get(hv, "_fr")
    if not hasattr(fr, "keys") or "state" not in fr:
        return {"host": name, "state": "not read", "error": "", "changes": [], "blocks": [], "by_hand": []}
    h = dict(fr)
    h["host"] = name
    ap = _get(hv, "_fr_apply")
    h["apply"] = dict(ap) if hasattr(ap, "keys") else None
    return h


def fapolicyd_rules_job_report(hostvars_list, names, since="today", title="", check=False):
    """[hostvars of each host], [their names], the ausearch -ts keyword, the report title, a Check run -> roles/site_email's
    _report: what fapolicyd blocked (or would, permissive), the changes to approve (trust entries, allow rules, the rules
    files' clean-up, permissive), what this run changed."""
    hosts = [_rules_host(n, hv) for n, hv in zip(names, hostvars_list)]
    read = [h for h in hosts if h["state"] == "read" and not h.get("error")]
    unread = [h for h in hosts if h["state"] == "not read" or (h["state"] == "read" and h.get("error"))]
    missing = [h["host"] for h in hosts if h["state"] == "not installed"]
    applied = [h for h in hosts if h.get("apply")]
    bad_states = ("failed", "rolled back", "refused")
    failed = [h for h in applied if h["apply"].get("state") in bad_states]
    changed = [h for h in applied if h["apply"].get("state") in ("changed", "would change")]
    done = {}
    for h in changed:
        done[h["host"]] = set(_norm(c.get("line")) for c in h["apply"].get("applied") or [])
    perm_hosts = [h for h in read if h.get("permissive") and not (h["apply"] or {}).get("permissive_after") == "0"]
    down = [h for h in read if (h["apply"] or {}).get("active_after", h.get("active")) != "active"]
    denied = sum(g["count"] for h in read for g in h.get("blocks") or [] if g["kind"] == "denied")
    would = sum(g["count"] for h in read for g in h.get("blocks") or [] if g["kind"] == "would deny")
    proposals = {}
    for h in read:
        for c in h.get("changes") or []:
            if _norm(c.get("line")) not in done.get(h["host"], set()):
                proposals.setdefault(c["line"], []).append((h["host"], c))
    by_hand = [(h["host"], b) for h in read for b in h.get("by_hand") or []]
    status = "critical" if failed or down or perm_hosts else ("warning" if proposals or denied or would or unread else "ok")
    head = []
    if denied or would:
        head.append("%d block(s)%s on %d host(s)" % (denied + would, (", %d of them would-be (permissive)" % would) if would else "",
                                                     len([h for h in read if h.get("blocks")])))
    if proposals:
        head.append("%d change(s) to approve" % len(proposals))
    if changed:
        head.append(("would change %d host(s)" if check else "changed on %d host(s)") % len(changed))
    if failed:
        head.append("not changed on %d host(s)" % len(failed))
    if perm_hosts:
        head.append("permissive on %d" % len(perm_hosts))
    if down:
        head.append("fapolicyd not running on %d" % len(down))
    report = {
        "title": "%s: %s" % (title or "fapolicyd rules", ", ".join(head) if head else "nothing blocked since %s, nothing to change" % since),
        "subtitle": "%d host(s) read - what fapolicyd blocked since %s (ausearch -m FANOTIFY -ts %s), the trust entries and rules "
                    "to let it through, the rules files' clean-up%s" % (len(read), since, since, " - Check: nothing changed" if check else ""),
        "status": status,
        "summary": [{"label": "Blocked", "value": denied, "status": "warning" if denied else "ok"},
                    {"label": "Would be blocked (permissive)", "value": would, "status": "warning" if would else "ok"},
                    {"label": "Changes to approve", "value": len(proposals), "status": "warning" if proposals else "ok"}]
                   + ([{"label": "Hosts changed" if not check else "Hosts it would change", "value": len(changed), "status": "ok"}] if applied else [])
                   + ([{"label": "Not changed", "value": len(failed), "status": "critical"}] if failed else [])
                   + [{"label": "Permissive", "value": len(perm_hosts), "status": "critical" if perm_hosts else "ok"},
                      {"label": "For a person", "value": len(by_hand), "status": "warning" if by_hand else "ok"},
                      {"label": "Hosts read", "value": len(read)}]
                   + ([{"label": "Not read", "value": len(unread), "status": "warning"}] if unread else []),
        "sections": [],
        "footer": RNOTE,
    }
    secs = report["sections"]
    if applied:
        rows, rs = [], []
        for h in applied:
            a = h["apply"]
            st = a.get("state", "")
            why = "; ".join([x for x in [a.get("error"), a.get("note"), a.get("back")] if x]
                            + ["%s: %s" % (b["line"], b["why"]) for b in (a.get("bad") or []) + (a.get("refused") or [])])
            rows.append([h["host"], st, "\n".join(c["line"] for c in a.get("applied") or []) or "-",
                         "%s -> %s" % (h.get("active", "?"), a.get("active_after") or h.get("active", "?")),
                         "\n".join(a.get("not_here") or []) or "-", why or "-"])
            rs.append("critical" if st in bad_states else "ok")
        secs.append({"title": "What this run changed" if not check else "What this run would change", "status": "critical" if failed else "ok",
                     "text": "The changes approved (fapolicyd_approved) that this run proposed again on the host. Then: the rules "
                             "loaded (fagenrules --load), the trust reloaded (fapolicyd-cli --update) or fapolicyd restarted (permissive "
                             "changed), checked in the same go - running, a program (python) still starts, the rules as approved. Running "
                             "before and not after: every file put back." + (" Check: nothing was changed." if check else ""),
                     "columns": ["Host", "Result", "Changed", "fapolicyd", "Not proposed here", "Why"], "rows": rows, "row_status": rs})
    used = set()
    for h in applied:
        a = h["apply"]
        used |= set(_norm(c.get("line")) for c in a.get("applied") or [])
        used |= set(_norm(b.get("line")) for b in (a.get("refused") or []) + (a.get("bad") or []))
    idle = sorted(set(_norm(x) for h in applied for x in h["apply"].get("not_here") or []) - used)
    if idle:
        secs.append({"title": "Approved, made on no host", "status": "warning",
                     "text": "Lines in fapolicyd_approved that no host of this run has now: made already (a run before), for a "
                             "host outside this run's Limit, a file that changed since (its size or hash), or not exactly the report's "
                             "line. Nothing was changed for them.", "lines": idle})
    if proposals:
        secs.append({"title": "Changes to approve", "status": "warning",
                     "text": "Each line is a change (its hosts, why and what it does: below). To make them, after your security approval, "
                             "list the lines in fapolicyd_approved (the template's Variables) exactly as here and run the job again - "
                             "a Check run first shows what it would change. Trust file: that file, as it is now (its size and sha256: a "
                             "changed file is a new line), added to the job's own trust file. Allow rule: a container's file, to the job's "
                             "own rules file. Remove rule: the rule commented out in its file, marked. A change that blocks more is made only "
                             "while fapolicyd is permissive.",
                     "lines": [line for line, _ in sorted(proposals.items(), key=lambda x: (RULES_ORDER.get(x[1][0][1].get("kind"), 9), x[0]))]})
        rows, rs = [], []
        for line, hs in sorted(proposals.items(), key=lambda x: (RULES_ORDER.get(x[1][0][1].get("kind"), 9), x[0])):
            c = hs[0][1]
            rows.append([", ".join(sorted(set(hn for hn, _ in hs))), line, c.get("why", ""), c.get("effect", "")])
            rs.append("critical" if c.get("stricter") or c.get("kind") == "permissive" else "warning")
        secs.append({"title": "Why each change", "columns": ["Hosts", "Change", "Why", "What it does"], "rows": rows, "row_status": rs})
    blocks = [(h["host"], g) for h in read for g in h.get("blocks") or []]
    if blocks:
        rows, rs = [], []
        for hn, g in sorted(blocks, key=lambda x: (x[1]["kind"] != "denied", -x[1]["count"], x[0])):
            rows.append([hn, "blocked" if g["kind"] == "denied" else "would be (permissive)", g["exe"] or "?", g["path"] or "?", g["perm"],
                         g["count"], g.get("last", ""), ("%s. %s" % (g["rule_no"], g["rule"])) if g.get("rule") else (g.get("rule_no") or "?"),
                         g.get("where", ""), g.get("change") or g.get("note") or "-"])
            rs.append("warning" if g["kind"] == "denied" else "info")
        secs.append({"title": "What fapolicyd blocked", "status": "warning",
                     "text": "Since %s: the program, the file, how often, the rule (its text only for blocks since the rules last "
                             "changed - an older block's number may point at another rule now), where the file is, and the change "
                             "proposed. Would be: fapolicyd is permissive - it let it through and logged it." % since,
                     "columns": ["Host", "Blocked", "Program", "File", "Permission", "Times", "Last", "Rule", "File on", "Change"],
                     "rows": rows, "row_status": rs})
    if by_hand:
        secs.append({"title": "For a person", "status": "warning",
                     "text": "What the job does not change: decide it with the system's owner and your security approval.",
                     "lines": ["%s: %s" % x for x in by_hand]})
    if read:
        rows, rs = [], []
        for h in read:
            rows.append([h["host"], h.get("active", "?"), "yes" if h.get("permissive") else "no", h.get("auditd", "?"), h.get("version", ""),
                         len(h.get("blocks") or []), len(h.get("changes") or [])])
            rs.append("critical" if h.get("active") != "active" or h.get("permissive") else ("warning" if h.get("changes") else "ok"))
        secs.append({"title": "The hosts", "status": "critical" if down or perm_hosts else ("warning" if proposals else "ok"),
                     "text": "fapolicyd on each host: running, permissive (it blocks nothing: a STIG finding), auditd (without it no "
                             "block is recorded).",
                     "columns": ["Host", "fapolicyd", "Permissive", "auditd", "Version", "Block groups", "Changes"], "rows": rows, "row_status": rs})
    for h in read:
        j = (h["apply"] or {}).get("journal") or []
        if j:
            secs.append({"title": "%s: fapolicyd's journal" % h["host"], "status": "critical" if (h["apply"] or {}).get("state") in bad_states else "info",
                         "text": "fapolicyd's own lines after this run's change.", "lines": [x[:400] for x in j]})
    if missing:
        secs.append({"title": "fapolicyd not installed", "lines": ["%d host(s), not looked at: %s" % (len(missing), ", ".join(sorted(missing)))]})
    if unread:
        secs.append({"title": "Not read", "status": "warning",
                     "lines": ["%s: %s" % (h["host"], h.get("error") or "could not be reached, or failed before it was read")
                               for h in sorted(unread, key=lambda h: h["host"])]})
    return report


# ---- the fapolicyd job (playbooks/fapolicyd.yml, roles/fapolicyd): the fix and the rules, one plan, one report ------
def fapolicyd_split(lines):
    """The approved lines -> {fix: [...], rules: [...]}: each to the part that gives it (permissive lines to both - each
    makes it only where it proposes it); a line neither gives goes to both, so each refuses everything."""
    fix, rules = [], []
    for a in lines or []:
        a = _norm(a)
        if not a:
            continue
        f, r = any(x.match(a) for x in FIX_FORM), any(x.match(a) for x in RULES_FORM)
        if f or not r:
            fix.append(a)
        if r or not f:
            rules.append(a)
    return {"fix": fix, "rules": rules}


def fapolicyd_workflow(hostvars_list, names):
    """The workflow's plan (playbooks/fapolicyd.yml with fapolicyd_workflow: true): {host: [lines]} - what the approval
    step approves for each host and the approved job makes - and what it holds back for a person [(host, line, why)].
    Made: what gets fapolicyd running - trust files it cannot read, labels, db_max_size, enabled at boot, and a stopped
    fapolicyd started permissive (it blocks nothing) - then trust entries, container rules and the clean-up. Held:
    permissive: 0 -> 1 on a running, enforcing host; a clean-up that blocks more on an enforcing host; permissive: 1 -> 0
    while fapolicyd is not running, has would-be blocks in the window, or a clean-up that blocks more to make first -
    enforcing comes on a quiet day, after the rest."""
    plan, held = {}, []
    for n, hv in zip(names, hostvars_list):
        ff, fr = _fix_host(n, hv), _rules_host(n, hv)
        lines = []
        if ff["state"] == "read" and not ff.get("error"):
            lines += [_norm(f.get("line")) for f in ff.get("fixes") or []]
        if fr["state"] == "read" and not fr.get("error"):
            changes = fr.get("changes") or []
            would = sum(g.get("count", 0) for g in fr.get("blocks") or [] if g.get("kind") == "would deny")
            stricter = [c for c in changes if c.get("stricter")]
            running = fr.get("active") == "active"
            for c in changes:
                line = _norm(c.get("line"))
                if line == "permissive: 0 -> 1":
                    held.append((n, line, "fapolicyd would stop blocking: a person decides"))
                elif c.get("stricter") and not fr.get("permissive"):
                    held.append((n, line, "it blocks more and fapolicyd is enforcing: only while permissive"))
                elif line == "permissive: 1 -> 0" and (would or stricter or not running):
                    held.append((n, line, ("fapolicyd is not running: it starts permissive first" if not running else
                                           "%d would-be block(s) in the window: their changes first, enforcing on a quiet day" % would
                                           if would else "a clean-up that blocks more comes first, while permissive")))
                elif line not in lines:
                    lines.append(line)
        if lines:
            plan[n] = lines
    return {"plan": plan, "held": held, "count": sum(len(v) for v in plan.values())}


def fapolicyd_act_findings(fr, ff=None):
    """What ACT is asked about on one host: fapolicyd's state and its blocks, in a line each."""
    fr, ff = fr or {}, ff or {}
    out = []
    if fr.get("active") != "active":
        out.append("fapolicyd is %s: nothing is allow-listed" % (fr.get("active") or "not running"))
    if fr.get("permissive"):
        out.append("fapolicyd is permissive: it blocks nothing, it logs what it would block")
    for g in (fr.get("blocks") or [])[:20]:
        out.append("fapolicyd %s %s -> %s (%s) x%d" % ("blocked" if g.get("kind") == "denied" else "would block",
                                                      g.get("exe") or "?", g.get("path") or "?", g.get("perm", "?"), g.get("count", 0)))
    return out


def fapolicyd_act_evidence(fr, ff=None):
    """The job's own reading of one host, for ACT: each block group with the rule that decided it and the change the job
    proposes (or why a person must decide), what keeps fapolicyd down, the rules files' review."""
    fr, ff = fr or {}, ff or {}
    out = ["### The job's reading of this host (fapolicyd %s%s)" % (fr.get("active", "?"), ", permissive" if fr.get("permissive") else "")]
    for g in fr.get("blocks") or []:
        out.append("- %s %s -> %s (%s) x%d, last %s, rule %s%s; where: %s; the job proposes: %s" % (
            "blocked" if g.get("kind") == "denied" else "would block", g.get("exe") or "?", g.get("path") or "?", g.get("perm", "?"),
            g.get("count", 0), g.get("last", "?"), g.get("rule_no") or "?", (" (%s)" % g["rule"]) if g.get("rule") else "",
            g.get("where", "?"), g.get("change") or g.get("note") or "nothing"))
    for f in ff.get("fixes") or []:
        out.append("- what keeps it down: %s - %s" % (f.get("line"), f.get("why", "")))
    for f in fr.get("review") or []:
        out.append("- rules file %s%s: %s - %s" % (f.get("file"), (" line %s" % f["line"]) if f.get("line") else "", f.get("rule") or "", f.get("what", "")))
    for b in (fr.get("by_hand") or []) + (ff.get("by_hand") or []):
        out.append("- for a person: %s" % b)
    return "\n".join(out)


def fapolicyd_all_report(hostvars_list, names, since="today", title="", check=False, workflow=False):
    """The fapolicyd job's one report: getting fapolicyd running (the fix's report) and its blocks, trust entries and
    rules (the rules' report), one list of changes to approve, ACT's analysis when it ran (fapolicyd_act); the workflow's
    check (workflow): what Approve makes on each host, and what is held back for a person."""
    fx = fapolicyd_fix_report(hostvars_list, names, "", check)
    ru = fapolicyd_rules_job_report(hostvars_list, names, since, "", check)
    fs = dict((x["title"], x) for x in fx["sections"])
    rs = dict((x["title"], x) for x in ru["sections"])
    order = {"critical": 0, "warning": 1, "ok": 2}
    status = min([fx["status"], ru["status"]], key=lambda x: order.get(x, 2))
    head = []
    for t in (fx["title"], ru["title"]):
        for part in t.split(": ", 1)[-1].split(", "):
            if part not in head and not part.startswith(("nothing to fix", "nothing blocked")):
                head.append(part)
    lines = list((fs.get("Fixes to approve") or {}).get("lines") or []) + list((rs.get("Changes to approve") or {}).get("lines") or [])
    by_label = dict((x["label"], x) for x in ru["summary"])
    summary = [by_label["Blocked"], by_label["Would be blocked (permissive)"],
               {"label": "Changes to approve", "value": len(lines), "status": "warning" if lines else "ok"}]
    summary += [x for x in fx["summary"] if x["label"] in ("fapolicyd not running", "Hosts fixed", "Hosts it would fix", "Not fixed")]
    summary += [x for x in ru["summary"] if x["label"] in ("Hosts changed", "Hosts it would change", "Not changed", "Permissive",
                                                            "For a person", "Hosts read", "Not read")]
    report = {"title": "%s: %s" % (title or "fapolicyd", ", ".join(head) if head else "running, nothing blocked since %s, nothing to change" % since),
              "subtitle": "%s - and what keeps fapolicyd down: its trust files, SELinux labels, db_max_size, the service"
                          % ru["subtitle"],
              "status": status, "summary": summary, "sections": [], "footer": RNOTE}
    secs = report["sections"]
    for t, new in (("What this run fixed", "Getting fapolicyd running: what this run did"),
                   ("What this run would fix", "Getting fapolicyd running: what this run would do")):
        if t in fs:
            secs.append(dict(fs[t], title=new))
    for t, new in (("What this run changed", "Trust and rules: what this run did"),
                   ("What this run would change", "Trust and rules: what this run would do")):
        if t in rs:
            secs.append(dict(rs[t], title=new))
    # approved lines no part has made: the union of each part's "not here" less what either made or refused
    used, idle = set(), set()
    for n, hv in zip(names, hostvars_list):
        for ap in (_get(hv, "_ff_apply"), _get(hv, "_fr_apply")):
            if hasattr(ap, "keys"):
                used |= set(_norm(c.get("proposed") or c.get("line")) for c in ap.get("applied") or [])
                used |= set(_norm(c.get("line")) for c in ap.get("applied") or [])
                used |= set(_norm(b.get("line")) for b in (ap.get("refused") or []) + (ap.get("bad") or []))
                idle |= set(_norm(x) for x in ap.get("not_here") or [])
    if idle - used:
        secs.append({"title": "Approved, made on no host", "status": "warning",
                     "text": "Lines in fapolicyd_approved that no host of this run has now: made already, for a host outside this "
                             "run's Limit, a file that changed since, or not exactly the report's line. Nothing was changed for them.",
                     "lines": sorted(idle - used)})
    if lines:
        rows = [[r[0], r[1], r[2], "gets fapolicyd running"] for r in (fs.get("Why each fix") or {}).get("rows") or []] \
            + list((rs.get("Why each change") or {}).get("rows") or [])
        rstat = list((fs.get("Why each fix") or {}).get("row_status") or []) + list((rs.get("Why each change") or {}).get("row_status") or [])
        secs.append({"title": "Changes to approve", "status": "warning",
                     "text": "Each line is a change (its hosts, why and what it does: below). Getting fapolicyd running first - its "
                             "trust files, labels, db_max_size, enabled at boot, started permissive - then trust entries for what it "
                             "blocked, container rules, the rules files' clean-up, and back to enforcing on a quiet day. To make them: "
                             "the workflow's approval step, or by hand - list them in fapolicyd_approved exactly as here and run the "
                             "job again (a Check run first). A db_max_size line may end in your own number.",
                     "lines": lines})
        secs.append({"title": "Why each change", "columns": ["Hosts", "Change", "Why", "What it does"], "rows": rows, "row_status": rstat})
    if workflow:
        w = fapolicyd_workflow(hostvars_list, names)
        if w["plan"]:
            secs.append({"title": "Approve makes these", "status": "warning",
                         "text": "What the workflow's approval step makes on each host - getting fapolicyd running first, then the "
                                 "rest. Deny makes none.",
                         "lines": ["%s: %s" % (h, l) for h in sorted(w["plan"]) for l in w["plan"][h]]})
        if w["held"]:
            secs.append({"title": "Held back for a person", "status": "warning",
                         "text": "Not in the approval: decide these by hand (fapolicyd_approved on a template of this playbook), or "
                                 "wait - enforcing comes on a quiet day by itself.",
                         "lines": ["%s: %s - %s" % x for x in w["held"]]})
    if "What fapolicyd blocked" in rs:
        secs.append(rs["What fapolicyd blocked"])
    for n, hv in zip(names, hostvars_list):
        a = _get(hv, "act_triage_result")
        if hasattr(a, "keys") and _get(hv, "_fa_act_ran"):
            secs.extend(act_sections(n, dict(a)))
    person = list((fs.get("For a person") or {}).get("lines") or []) + list((rs.get("For a person") or {}).get("lines") or [])
    for n, hv in zip(names, hostvars_list):
        if _get(hv, "_fa_rules_wait") is True or str(_get(hv, "_fa_rules_wait", "")).strip() == "True":
            person.insert(0, "%s: the trust and rules changes approved were not made - getting fapolicyd running failed first "
                             "(above); they come again once it runs" % n)
        if _get(hv, "_fa_act_skip"):
            person.append("%s: %s" % (n, _get(hv, "_fa_act_skip")))
    if person:
        secs.append({"title": "For a person", "status": "warning",
                     "text": "What the job does not change: decide it with the system's owner and your security approval.",
                     "lines": person})
    if "The hosts" in rs:
        secs.append(rs["The hosts"])
    if "The hosts" in fs:
        secs.append(dict(fs["The hosts"], title="The trust database"))
    seen = set()
    for x in fx["sections"] + ru["sections"]:
        if x["title"].endswith(": fapolicyd's journal") or x["title"] in ("fapolicyd not installed", "Not read"):
            if x["title"] not in seen:
                seen.add(x["title"])
                secs.append(x)
    return report


def _kind(line):
    for k, p in (("access", "trust file access: "), ("label", "SELinux label: "), ("db", "db_max_size: "), ("permissive", "permissive: "),
                 ("enable", "enable "), ("restart", "restart ")):
        if line.startswith(p):
            return k
    return ""


class FilterModule(object):
    def filters(self):
        return {"fapolicyd_report": fapolicyd_report, "fapolicyd_fix_plan": fapolicyd_fix_plan,
                "fapolicyd_fix_rollback": fapolicyd_fix_rollback, "fapolicyd_fix_report": fapolicyd_fix_report,
                "fapolicyd_rules_job_plan": fapolicyd_rules_job_plan, "fapolicyd_rules_job_edit": fapolicyd_rules_job_edit,
                "fapolicyd_rules_job_file": fapolicyd_rules_job_file, "fapolicyd_rules_job_rollback": fapolicyd_rules_job_rollback,
                "fapolicyd_rules_job_backups": fapolicyd_rules_job_backups, "fapolicyd_rules_job_report": fapolicyd_rules_job_report,
                "fapolicyd_split": fapolicyd_split, "fapolicyd_workflow": fapolicyd_workflow, "fapolicyd_all_report": fapolicyd_all_report,
                "fapolicyd_act_findings": fapolicyd_act_findings, "fapolicyd_act_evidence": fapolicyd_act_evidence}
