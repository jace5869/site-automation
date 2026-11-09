#!/usr/bin/env python3
"""Records for the ticket insights test (tests/servicenow/run_insights_test.sh, tests/test_servicenow_insights.py): a year of a
made-up site's ServiceNow - incidents of a dozen issues across five teams (names, phone numbers, host names and IP addresses
in their text, close notes good, thin and bare), requested items, catalog tasks, changes (some failed, followed by
incidents), problems, the assignment history (sys_audit) of incidents passed between groups, and work notes (sys_journal_field,
0.96.0: what was tried - the printers' restart a workaround used again and again; an address, a host and a phone number in
them that appear nowhere else). Deterministic.
    insights_seed.py OUT.json [SCALE] [--sites]      (SCALE 1 = about 1,500 incidents in the 6-month window)
--sites (0.97.0): the same records with the groups named as three made-up sites with their directorates - SITEA's staff
codes J6 and j63, SITEB's NOVA (the suffix) and an Other, SITE-C (a hyphen in it) with the named directorate CODEX."""
import datetime
import json
import random
import sys

R = random.Random(7)
END = datetime.datetime(2026, 11, 1, 0, 0, 0)          # the test's window ends here (until 2026-10-31)
START = END - datetime.timedelta(days=368)              # the window and the one before it, and a little more
SD, WIN, VDI, LNX, NET = "Ops - Service Desk", "Ops - Windows", "Ops - VDI", "Ops - Linux", "Ops - Network"
PEOPLE = {SD: ["Dana Whitfield", "Marcus Lee", "Priya Raman", "Tom Okafor", "Elena Ruiz"], WIN: ["Sam Patel", "Jordan Kim", "Alex Morgan"],
          VDI: ["Casey Brooks", "Morgan Diaz"], LNX: ["Avery Stone", "Quinn Harper"], NET: ["Taylor Grant", "Robin Shah"]}
# members of the groups in ServiceNow (sys_user_grmember) who hold no ticket: on the leaderboard with nothing resolved
QUIET = {WIN: ["Riley Chen"], NET: ["Noor Haddad"]}
CALLERS = ["%s %s" % (f, l) for f in ("Lee", "Maria", "Kevin", "Nina", "Omar", "Ruth", "Victor", "Wendy", "Brian", "Carla", "Derek",
                                      "Fiona", "Greg", "Hana", "Ivan", "Julia", "Keith", "Laura", "Mason", "Olga")
           for l in ("Anders", "Gomez", "Shaw", "Haddad", "Klein", "Ortiz", "Fields", "Nunez", "Walsh", "Turner", "Mori", "Petrov",
                     "Reyes", "Vance", "Quill", "Yates", "Zeller", "Lindqvist", "Okonkwo", "Barrett")]
BARE = ["fixed", "resolved", "done", "resolved per user", "see work notes", "rebooted", "n/a", "user confirmed"]

# name, weight, group weights, category, subcategory, descriptions, CIs, good notes, thin notes, bare share, hours, trend
ISSUES = [
    ("lockout", 14, {SD: .9, WIN: .1}, "Account", "Password", ["Account locked out - {caller}", "User locked out, call {phone}",
     "Password reset needed for {caller}", "Locked out of account after password change"], ["ad01", "ad02"],
     ["Unlocked the account in ADUC; Event 4740 showed the old password on the user's phone mail profile, removed it.",
      "Reset the password and unlocked the account; cleared saved credentials in Credential Manager.",
      "Unlocked the account and logged off a disconnected RDP session on {host} that held the old password."],
     ["Talked to the user, account working now", "User can log in again"], .12, 0.6, 0),
    ("cac", 11, {SD: .55, WIN: .45}, "Hardware", "Smart card", ["CAC not recognized on {wks}", "PIV certificate not showing at login",
     "Smart card reader not detected", "CAC certificates missing in browser"], ["wks-{n}"],
     ["Made the certificates available to Windows in ActivClient and restarted the Certificate Propagation service.",
      "Ran InstallRoot to add the DoD roots; certutil -scinfo then showed the full chain.", "Replaced the card reader; the CAC was read."],
     ["Reader issue", "Certificates issue on the workstation"], .15, 1.9, .25),
    ("horizon", 9, {SD: .3, VDI: .7}, "Software", "VDI", ["Horizon desktop black screen", "Cannot connect to VDI desktop",
     "Horizon session stuck connecting for {caller}", "VDI desktop not available in pool vdi-pool-ops"], ["vdi-pool-ops", "vdi-pool-eng"],
     ["Logged off the stuck session in Horizon Console and the user reconnected.", "Deleted the desktop in error state; the pool rebuilt it."],
     ["Desktop issue", "VDI problem resolved"], .42, 3.2, .4),
    ("outlook", 8, {SD: .6, WIN: .4}, "Software", "Email", ["Outlook will not open", "Outlook profile corrupt", "Outlook keeps asking for password"],
     ["exch01"], ["Created a new mail profile and set it as default; Outlook opened.", "Started Outlook in safe mode and disabled a broken add-in."],
     ["Outlook working"], .17, 2.4, 0),
    ("printer", 7, {SD: .4, WIN: .6}, "Hardware", "Printer", ["Printer offline on floor 2", "Print queue stuck on prn01", "Cannot print to {printer}"],
     ["prn01"], ["Deleted the stuck first job on prn01 and restarted the Print Spooler."],
     ["Printer issue", "Print problem", "Printer is working"], .62, 5.1, 0),
    ("share", 6, {SD: .5, WIN: .5}, "Access", "File share", ["Access denied to share on fs01", "Cannot open mapped drive S:", "Need access to the GIS folder"],
     ["fs01", "fs02"], ["Added the user to the owning group of the share after the data owner approved; klist purge picked it up."],
     ["Access granted"], .15, 1.4, 0),
    ("vpn", 5, {SD: .35, NET: .65}, "Network", "VPN", ["VPN drops every hour", "VPN will not connect from home", "VPN disconnects, IP {ip}"],
     ["vpn01", "vpn02"], ["Updated the VPN client to the current version; the session held.", "Renewed the user's expired VPN certificate."],
     ["VPN ok now"], .37, 4.6, .3),
    ("software", 4, {SD: .82, WIN: .18}, "Software", "Install", ["Please install Visio", "Need Adobe Pro installed", "Install software request for {caller}"],
     [], ["Installed the software from Software Center after the license was approved."], ["Installed"], .2, 2.9, 0),
    ("gpo", 3, {SD: .2, WIN: .72, VDI: .08}, "Software", "Login", ["Slow logon, takes 10 minutes", "Login very slow on {wks}"],
     ["ad01"], ["Removed a drive mapping to a retired server from the logon GPO; logon now 40 seconds."], ["Slow login fixed"], .51, 6.8, .5),
    ("disk", 3, {LNX: .95, NET: .05}, "Software", "Operating system", ["Filesystem full on app-db03", "/var full on web02, service stopped"],
     ["app-db03", "web02"], ["Rotated and compressed the logs with logrotate -f, cleared old dumps in /var/crash and restarted the service."],
     ["Cleaned up disk"], .1, 1.1, 0),
    ("port", 2, {SD: .1, NET: .9}, "Network", "LAN", ["Network drop dead in room 214", "No network at desk, port bldg4-acc-sw2 Gi1/0/14"],
     ["bldg4-acc-sw2"], ["Re-enabled the port shut by port security and replaced the patch cable."], ["Port fixed"], .28, 2.3, .6),
    ("teams", 4, {SD: .72, WIN: .28}, "Software", "Collaboration", ["Teams audio not working", "Teams camera not detected"],
     [], ["Selected the right device in Teams settings and cleared the Teams cache."], ["Teams ok"], .16, 1.2, 0),
]
CATALOG = [("New account / onboarding", 11, SD), ("Software install", 10, WIN), ("Access to shared drive", 9, WIN),
           ("Hardware: laptop or peripheral", 8, SD), ("Distribution list change", 5, SD), ("VPN token", 4, NET),
           ("Mobile device", 4, SD), ("Elevated privileges", 3, WIN), ("Linux server account", 2, LNX), ("Horizon desktop entitlement", 2, VDI)]


def t(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def when(day_frac):
    """A moment in the year, weekdays and working hours mostly."""
    for _ in range(20):
        d = START + datetime.timedelta(days=day_frac * 368 + R.uniform(-3, 3))
        if d.weekday() < 5 or R.random() < .05:
            break
    h = int(min(19, max(6, R.gauss(10.5, 2.6))))
    return d.replace(hour=h, minute=R.randint(0, 59), second=0)


SITES = {SD: "SITEA - J6 - Service Desk", WIN: "SITEA - j63 - Windows", VDI: "SITEB - Ops - VDI - nova", LNX: "SITE-C - CODEX - Linux",
         NET: "SITEB - Network"}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out_path = args[0]
    scale = float(args[1]) if len(args) > 1 else 1.0
    recs, n = [], [0]
    seq = {"INC": 40000, "RITM": 50000, "SCTASK": 60000, "CHG": 31000, "PRB": 4400}

    def num(p):
        seq[p] += 1
        return "%s%07d" % (p, seq[p])

    n_inc = int(3000 * scale)
    incs = []
    for _ in range(n_inc):
        iss = R.choices(ISSUES, weights=[i[1] for i in ISSUES])[0]
        name, w, gw, cat, sub, descs, cis, good, thin, bare_share, hours, trend = iss
        # later issues with a trend grow through the year
        frac = R.random() ** (1.0 / (1 + trend)) if trend else R.random()
        opened = when(frac)
        if opened >= END:
            continue
        group = R.choices(list(gw), weights=list(gw.values()))[0]
        caller = R.choice(CALLERS)
        n[0] += 1
        ctx = {"caller": caller, "phone": "(703) 555-%04d" % R.randint(100, 9999), "wks": "wks-%04d" % R.randint(1000, 1100),
               "host": "wks-%04d" % R.randint(1000, 1100), "printer": "prn01-fl%d" % R.randint(1, 4), "ip": "198.51.100.%d" % R.randint(2, 250),
               "n": "%04d" % R.randint(1000, 1100)}
        desc = R.choice(descs).format(**ctx)
        ci = R.choice(cis).format(**ctx) if cis else ""
        prio = R.choices(["1", "2", "3", "4", "5"], weights=[.001, .003, .3, .63, .066])[0]
        h = max(0.1, R.lognormvariate(0, .9) * hours)
        resolved = opened + datetime.timedelta(hours=h)
        open_still = resolved > END or R.random() < (.08 if group in (WIN, VDI) else .03) * max(0, (opened - (END - datetime.timedelta(days=60))).days / 60.0 + .3)
        who = R.choice(PEOPLE[group])
        r = R.random()
        note = R.choice(BARE) if r < bare_share else (R.choice(thin) if r < bare_share + .2 else R.choice(good).format(**ctx))
        if who == "Tom Okafor" and R.random() < .4:
            note = R.choice(BARE)
        rc = 0
        if R.random() < .06:
            rc = R.choice([3, 3, 4])
        rec = {"_table": "incident", "number": num("INC"), "short_description": desc, "category": cat, "subcategory": sub,
               "assignment_group": group, "assigned_to": who, "opened_by": R.choice(PEOPLE[SD]) if R.random() < .7 else caller,
               "caller_id": caller, "cmdb_ci": ci, "priority": "%s - %s" % (prio, ["Critical", "High", "Moderate", "Low", "Planning"][int(prio) - 1]),
               "opened_at": t(opened), "sys_created_on": t(opened), "reassignment_count": str(rc), "reopen_count": "0",
               "made_sla": "true" if R.random() < (.97 if h < hours * 2 else .7) else "false", "sys_updated_on": t(opened)}
        if open_still:
            rec.update({"state": "2", "resolved_at": "", "closed_at": "", "close_notes": ""})
        else:
            closed = resolved + datetime.timedelta(days=3)
            # some Windows tickets are closed by the service desk on the user's word: resolved by someone outside the team
            closer = R.choice(PEOPLE[SD]) if group == WIN and R.random() < .08 else who
            rec.update({"state": "7" if closed < END else "6", "resolved_at": t(resolved), "closed_at": t(closed) if closed < END else "",
                        "resolved_by": closer, "close_code": "Solution provided", "close_notes": note, "sys_updated_on": t(min(closed, END))})
        rec["_issue"] = name
        incs.append(rec)
    # P1/P2s, caused by failed changes
    chg_fail = []
    for i, (d, what, grp, outcome, ci) in enumerate([
            (datetime.datetime(2026, 6, 16, 22), "DoD root certificate update via GPO", WIN, "unsuccessful", "ad01"),
            (datetime.datetime(2026, 7, 13, 21), "Exchange hybrid certificate renewal", WIN, "unsuccessful", "exch01"),
            (datetime.datetime(2026, 8, 19, 20), "Horizon golden image push, vdi-pool-eng", VDI, "successful_issues", "vdi-pool-eng"),
            (datetime.datetime(2026, 9, 8, 23), "Core switch firmware, building 4", NET, "successful_issues", "bldg4-acc-sw2"),
            (datetime.datetime(2026, 10, 1, 22), "Monthly Linux patching (RHEL 9)", LNX, "unsuccessful", "web02")]):
        c = {"_table": "change_request", "number": num("CHG"), "short_description": what, "assignment_group": grp, "cmdb_ci": ci,
             "type": "normal" if i % 2 else "emergency", "state": "3", "opened_at": t(d - datetime.timedelta(days=5)),
             "work_start": t(d), "work_end": t(d + datetime.timedelta(hours=2)), "closed_at": t(d + datetime.timedelta(hours=6)),
             "close_code": outcome, "close_notes": "Backed out the change after the checks failed." if outcome == "successful_issues" else
             "Failed: the change did not apply.", "assigned_to": PEOPLE[grp][0]}
        recs.append(c)
        chg_fail.append(c)
        inc = {"_table": "incident", "number": num("INC"), "short_description": ["CAC sign-in failing site-wide after root update",
               "Email delivery stopped for all users", "VDI pool vdi-pool-eng unavailable", "Building 4 network down",
               "Patching left 40 Linux hosts without a service"][i], "category": "Software", "subcategory": "Outage",
               "assignment_group": grp, "assigned_to": PEOPLE[grp][0], "opened_by": PEOPLE[SD][0], "caller_id": CALLERS[i], "cmdb_ci": ci,
               "priority": "1 - Critical" if i in (1, 3) else "2 - High", "opened_at": t(d + datetime.timedelta(hours=8)),
               "resolved_at": t(d + datetime.timedelta(hours=12)), "closed_at": t(d + datetime.timedelta(days=3)), "state": "7",
               "resolved_by": PEOPLE[grp][0], "close_code": "Solution provided", "made_sla": "false", "reassignment_count": "1",
               "reopen_count": "0", "close_notes": "Rolled back the change; service restored after the rollback.",
               "caused_by": c["number"] if i != 2 else "", "sys_updated_on": t(d + datetime.timedelta(days=3))}
        recs.append(inc)
    for i in range(int(60 * scale)):
        d = when(.5 + R.random() * .5)
        grp = R.choice([WIN, VDI, LNX, NET])
        recs.append({"_table": "change_request", "number": num("CHG"), "short_description": "Routine change %d" % i, "assignment_group": grp,
                     "type": "emergency" if R.random() < .1 else "normal", "state": "3", "opened_at": t(d - datetime.timedelta(days=4)),
                     "work_end": t(d), "closed_at": t(d + datetime.timedelta(hours=4)), "close_code": "successful", "cmdb_ci": ""})
    # problems: the Horizon pool's (open), the CAC one (resolved, the incidents go on)
    prb_h = {"_table": "problem", "number": num("PRB"), "short_description": "Horizon sessions fail on vdi-pool-ops", "assignment_group": VDI,
             "cmdb_ci": "vdi-pool-ops", "state": "2", "opened_at": t(datetime.datetime(2026, 7, 1, 9)), "closed_at": "", "resolved_at": ""}
    prb_c = {"_table": "problem", "number": num("PRB"), "short_description": "CAC certificates not propagated", "assignment_group": SD,
             "cmdb_ci": "wks-1010", "state": "3", "opened_at": t(datetime.datetime(2026, 6, 20, 9)),
             "resolved_at": t(datetime.datetime(2026, 7, 15, 9)), "closed_at": t(datetime.datetime(2026, 7, 20, 9))}
    recs += [prb_h, prb_c]
    for rec in incs:
        if rec["_issue"] == "horizon" and rec["cmdb_ci"] == "vdi-pool-ops" and rec["opened_at"] > prb_h["opened_at"] and R.random() < .5:
            rec["problem_id"] = prb_h["number"]
        if rec["_issue"] == "cac" and rec["cmdb_ci"] == "wks-1010":
            rec["problem_id"] = prb_c["number"]
    # reopened incidents only on a site that reopens (SCALE given with a "r"): none here - the report counts "came back"
    # hand-offs: the assignment history of the incidents reassigned 3+ times
    for rec in incs:
        rc = int(rec["reassignment_count"])
        if rc < 3:
            continue
        grp = rec["assignment_group"]
        path = {WIN: [SD, WIN, SD, WIN], VDI: [SD, VDI, WIN, VDI], NET: [SD, NET, WIN, NET], LNX: [SD, LNX, NET, LNX],
                SD: [SD, WIN, NET, SD]}[grp]
        o = datetime.datetime.strptime(rec["opened_at"], "%Y-%m-%d %H:%M:%S")
        for k in range(1, len(path)):
            recs.append({"_table": "sys_audit", "tablename": "incident", "fieldname": "assignment_group", "documentkey": "",
                         "_doc": rec["number"], "oldvalue": path[k - 1], "newvalue": path[k], "sys_created_on": t(o + datetime.timedelta(hours=k * 5))})
        # the same path as the out-of-box incident metric "Assignment Group" (metric_instance, 0.97.0)
        for k, g in enumerate(path):
            recs.append({"_table": "metric_instance", "definition": "Assignment Group", "table": "incident", "_doc": rec["number"], "value": g,
                         "start": t(o + datetime.timedelta(hours=k * 5)), "end": t(o + datetime.timedelta(hours=(k + 1) * 5)) if k + 1 < len(path) else ""})
        rec["reassignment_count"] = str(len(path) - 1)
        if rec.get("resolved_at"):
            rd = datetime.datetime.strptime(rec["resolved_at"], "%Y-%m-%d %H:%M:%S") + datetime.timedelta(days=2)
            rec["resolved_at"] = t(rd)
    # requested items and their catalog tasks
    for _ in range(int(1800 * scale)):
        cat, w, grp = R.choices(CATALOG, weights=[c[1] for c in CATALOG])[0]
        opened = when(R.random())
        if opened >= END:
            continue
        days = max(.1, R.lognormvariate(0, .7) * (6.8 if "Hardware" in cat else 2))
        closed = opened + datetime.timedelta(days=days)
        done = closed < END and R.random() > .04
        who = R.choice(PEOPLE[grp])
        ritm = {"_table": "sc_req_item", "number": num("RITM"), "short_description": cat, "cat_item": cat, "assignment_group": grp,
                "assigned_to": who, "opened_by": R.choice(CALLERS), "requested_for": R.choice(CALLERS), "opened_at": t(opened),
                "state": "3" if done else "1", "closed_at": t(closed) if done else "", "made_sla": "true" if R.random() < .93 else "false"}
        recs.append(ritm)
        if R.random() < .3:
            recs.append({"_table": "sc_task", "number": num("SCTASK"), "short_description": cat, "request_item": ritm["number"],
                         "assignment_group": grp, "assigned_to": who, "opened_at": t(opened), "state": "3" if done else "1",
                         "closed_at": t(closed) if done else "", "closed_by": who if done else "",
                         "close_notes": R.choice(["Completed", "Done", "Created the account and sent the welcome email.",
                                                  "Installed the software and confirmed with the user."]) if done else ""})
    # work notes (0.96.0): their own random numbers (R2), so every record above stays as it was
    R2 = random.Random(11)
    tried = {"printer": ["Restarted the Print Spooler on {printer}; the queue cleared but jammed again the next day.",
                         "Workaround again: restarted the spooler on {printer}, same as last week. Driver looks old.",
                         "Updated the print driver on {printer} to the vendor's current version; no jams since."],
             "lockout": ["Called {caller} at {phone}; the old password is on the phone's mail profile.",
                         "Removed the stale credential from the phone, asked the user to watch for lockouts."],
             "vpn": ["Client log shows the certificate expired; renewed it, emailed vpn-help.wn@example.mil for the new profile.",
                     "VPN gateway wn-gw-07 dropped the session at the hour; asked the user to update the client."]}
    for rec in incs:
        lines = tried.get(rec.get("_issue"))
        if not lines or not rec.get("resolved_at") or R2.random() < .4:
            continue
        o = datetime.datetime.strptime(rec["opened_at"], "%Y-%m-%d %H:%M:%S")
        ctx = {"printer": "prn01-fl%d" % R2.randint(1, 4), "caller": rec["caller_id"], "phone": "(571) 555-%04d" % R2.randint(100, 9999)}
        for k, line in enumerate(lines[:R2.randint(1, len(lines))]):
            recs.append({"_table": "sys_journal_field", "element": "work_notes", "name": "incident", "_doc": rec["number"],
                         "value": line.format(**ctx), "sys_created_on": t(o + datetime.timedelta(minutes=20 * (k + 1)))})
    for rec in incs:
        rec.pop("_issue", None)
    # the groups' members (sys_user_grmember): the people, and the quiet members
    for grp in (SD, WIN, VDI, LNX, NET):
        for who in PEOPLE[grp] + QUIET.get(grp, []):
            recs.append({"_table": "sys_user_grmember", "user": who, "group": grp, "sys_created_on": t(START)})
    recs = incs + recs
    for x in recs:
        if x.get("_table") == "sys_audit":
            x["documentkey"] = "s-" + x.pop("_doc")
        if x.get("_table") == "sys_journal_field":
            x["element_id"] = "s-" + x.pop("_doc")
        if x.get("_table") == "metric_instance":
            x["id"] = "s-" + x.pop("_doc")
    for x in recs:
        if x.get("_table") not in ("sys_audit", "sys_user_group", "metric_instance") and x.get("number"):
            x["sys_id"] = "s-" + x["number"]
    recs.sort(key=lambda x: x.get("opened_at") or x.get("sys_created_on") or "")
    if "--sites" in sys.argv:              # the groups renamed only: every record as it was otherwise
        for x in recs:
            for k in ("assignment_group", "group", "oldvalue", "newvalue", "value"):
                if x.get(k) in SITES:
                    x[k] = SITES[x[k]]
        # the groups in ServiceNow (sys_user_group): the sites' roots, a staff division, a functional parent, a cycle,
        # an inactive group - for the parents (0.97.0)
        tree = [("gsa", "SITEA", "", "true", ""), ("gsb", "SITEB", "", "true", ""), ("gsc", "SITE-C", "", "true", ""),
                ("gsa6", "SITEA - J6 Staff Division", "gsa", "true", ""), ("gbcs", "SITEB - Client Support", "gsb", "true", ""),
                ("gcl", "SITE-C - Loop", "g-lnx", "true", ""),
                ("g-sd", SITES[SD], "gsa6", "true", ""), ("g-win", SITES[WIN], "gsa", "true", ""), ("g-vdi", SITES[VDI], "gsb", "true", ""),
                ("g-net", SITES[NET], "gbcs", "false", ""), ("g-lnx", SITES[LNX], "gcl", "true", "")]
        recs += [{"_table": "sys_user_group", "sys_id": k, "name": n, "parent": p, "active": a, "type": ty} for k, n, p, a, ty in tree]
    with open(out_path, "w") as fh:
        json.dump(recs, fh)
    print("%d records (%d incidents) -> %s" % (len(recs), sum(1 for x in recs if x.get("_table") == "incident"), out_path))


if __name__ == "__main__":
    main()
