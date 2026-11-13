#!/usr/bin/env python3
"""A made-up site for About to breach (1.59.0, tests/servicenow/run_breach_test.sh): three groups, open tickets with SLAs
running against a fixed clock (2026-11-13 07:00 UTC) - three within the hour (one assigned to nobody, one with a response
and a resolution SLA), two within 4 hours (one with no business time left of ServiceNow's: its planned end counts), one
within 8, one outside the window, one paused, one on a closed ticket - and the breaches of the last weeks: 13, 16 and 21,
with a cancelled one and one too old (both left out). Every name is made up.
    python3 tests/servicenow/breach_seed.py OUT.json"""
import datetime
import json
import sys

NOW = datetime.datetime(2026, 11, 13, 7, 0, 0)
GROUPS = [("g-net", "Network Operations"), ("g-sd2", "Service Desk Tier 2"), ("g-virt", "Virtualization")]


def t(d):
    return d.strftime("%Y-%m-%d %H:%M:%S")


def dur(minutes):
    """A ServiceNow duration (a date after the epoch)."""
    return t(datetime.datetime(1970, 1, 1) + datetime.timedelta(minutes=minutes))


def main(out):
    recs = [{"_table": "sys_user_group", "sys_id": g, "name": n, "active": "true", "parent": ""} for g, n in GROUPS]
    opened = t(NOW - datetime.timedelta(days=2))

    def ticket(num, table, group, who, short, prio="3 - Moderate", state="2", caller="Sam Lee", **kw):
        r = {"_table": table, "sys_id": "s-" + num, "number": num, "short_description": short, "priority": prio, "state": state,
             "assignment_group": group, "assigned_to": who, "opened_at": opened, "sys_created_on": opened, "sys_updated_on": opened,
             "opened_by": "Sam Lee"}
        r["caller_id" if table == "incident" else "requested_for"] = caller
        r.update(kw)
        recs.append(r)

    def running(num, sla, left_min, used, left=True, stage="in_progress", target=""):
        r = {"_table": "task_sla", "task": "s-" + num, "sla": sla, "stage": stage, "has_breached": "false", "business_percentage": "%.1f" % used,
             "percentage": "%.1f" % used, "start_time": opened, "planned_end_time": t(NOW + datetime.timedelta(minutes=left_min))}
        if left:
            r["business_time_left"] = dur(left_min)
        if target:
            r["_target"] = target
        recs.append(r)

    ticket("XINC0041872", "incident", "Network Operations", "Admin C", "VPN drops every 20 minutes for SITEB users", "2 - High", caller="Pat Doe")
    running("XINC0041872", "P2 Incident Response", 24, 96)
    running("XINC0041872", "P2 Incident Resolution", 300, 40)
    ticket("XINC0041901", "incident", "Service Desk Tier 2", "", "Shared drive H: not mapping after password change")
    running("XINC0041901", "P3 Incident Resolution", 42, 93)
    ticket("XRITM0019220", "sc_req_item", "Virtualization", "Admin A", "New VM for the test lab (4 vCPU, 16 GB)", caller="Pat Doe")
    running("XRITM0019220", "Request Fulfillment Resolution", 54, 91)
    ticket("XINC0041915", "incident", "Network Operations", "Admin B", "Printer queue on print02 stuck", "2 - High")
    running("XINC0041915", "P2 Incident Resolution", 96, 84, left=False)                     # no business time left: its planned end
    ticket("XINC0041930", "incident", "Service Desk Tier 2", "", "Outlook search returns nothing")
    running("XINC0041930", "P3 Incident Resolution", 138, 78)
    ticket("XINC0041977", "incident", "Virtualization", "Admin A", "Slow logon on VDI pool SITEA-VDI")
    running("XINC0041977", "P3 Incident Resolution", 426, 36)
    ticket("XINC0041990", "incident", "Network Operations", "Admin B", "Switch port flaps in the lab")
    running("XINC0041990", "P4 Incident Resolution", 720, 20)                               # 12 h: outside the window
    ticket("XINC0041995", "incident", "Service Desk Tier 2", "Admin E", "Awaiting the user", state="3")
    running("XINC0041995", "P3 Incident Resolution", 30, 95, stage="paused")                 # paused: does not run down
    ticket("XINC0041998", "incident", "Network Operations", "Admin C", "Closed but its SLA still runs", state="7",
           closed_at=t(NOW - datetime.timedelta(hours=1)), resolved_at=t(NOW - datetime.timedelta(hours=2)))
    running("XINC0041998", "P3 Incident Resolution", 20, 97)                                 # its ticket closed: left out
    # the breaches: offset 0 = today (before 07:00), the last week 13, the one before 16, the one before that 21, older 2 a day
    per_day = [2, 2, 1, 2, 2, 2, 2] + [3, 2, 2, 3, 2, 2, 2] + [3] * 7 + [2] * 15
    k = 0
    for off, cnt in enumerate(per_day):
        for i in range(cnt):
            k += 1
            num = "XINC00400%02d" % k if k < 100 else "XINC0040%03d" % k
            grp = GROUPS[0][1] if (off < 7 and k % 3 != 0) else GROUPS[k % 3][1]
            when = (NOW - datetime.timedelta(days=off)).replace(hour=1 + 2 * i, minute=0)
            ticket(num, "incident", grp, "Admin F", "A breached one", state="7", closed_at=t(when + datetime.timedelta(hours=3)),
                   resolved_at=t(when + datetime.timedelta(hours=2)))
            recs.append({"_table": "task_sla", "task": "s-" + num, "sla": "P3 Incident %s" % ("Response" if k % 5 == 0 else "Resolution"),
                         "stage": "breached", "has_breached": "true", "business_percentage": "120.0", "start_time": t(when - datetime.timedelta(days=1)),
                         "planned_end_time": t(when)})
    ticket("XINC0040998", "incident", "Network Operations", "Admin F", "Cancelled", state="7", closed_at=t(NOW - datetime.timedelta(days=1)))
    recs.append({"_table": "task_sla", "task": "s-XINC0040998", "sla": "P3 Incident Resolution", "stage": "cancelled", "has_breached": "true",
                 "planned_end_time": t(NOW - datetime.timedelta(days=1, hours=2))})
    ticket("XINC0040999", "incident", "Network Operations", "Admin F", "Too old", state="7", closed_at=t(NOW - datetime.timedelta(days=60)))
    recs.append({"_table": "task_sla", "task": "s-XINC0040999", "sla": "P3 Incident Resolution", "stage": "breached", "has_breached": "true",
                 "planned_end_time": t(NOW - datetime.timedelta(days=60))})
    with open(out, "w") as fh:
        json.dump(recs, fh, indent=1)
    print(len(recs))


if __name__ == "__main__":
    main(sys.argv[1])
