# -*- coding: utf-8 -*-
"""The Tenable.sc scan job (playbooks/tenable_scan.yml, roles/tenable_scan): the targets from the inventory, and the report of
what site_tenable_scan read, chose and launched - the zone of each host, the scans, the findings when it waited, and
what Tenable.sc offers when nothing was launched. One report (roles/site_email's _report)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3}


def _get(hv, key, default=None):
    try:
        v = hv[key] if key in hv else default
    except Exception:            # a variable that does not template is as good as missing
        return default
    return default if v is None else v


def tenable_scan_targets(names, hostvars, extra=None, exclude=("localhost", "127.0.0.1")):
    """Inventory host names (tenable_scan_target's) with their hostvars, and names or addresses not in the inventory
    (tenable_scan_targets) -> [{name, address, zone, groups}] for site_tenable_scan, each host once."""
    out, seen = [], set()
    for n in names or []:
        n = str(n)
        if n in exclude or n in seen:
            continue
        seen.add(n)
        try:
            hv = hostvars[n]
        except Exception:        # noqa: BLE001 - a name the inventory does not know: itself
            hv = {}
        out.append({"name": n, "address": str(_get(hv, "ansible_host", "") or _get(hv, "tenable_address", "") or n),
                    "zone": str(_get(hv, "tenable_zone", "") or ""), "groups": [str(g) for g in (_get(hv, "group_names", []) or [])]})
    for x in extra or []:
        if isinstance(x, dict):
            n = str(x.get("name") or x.get("address") or "")
            rec = {"name": n, "address": str(x.get("address") or n), "zone": str(x.get("zone") or ""), "groups": [str(g) for g in (x.get("groups") or [])]}
        else:
            n = str(x)
            rec = {"name": n, "address": n, "zone": "", "groups": []}
        if n and n not in seen and n not in exclude:
            seen.add(n)
            out.append(rec)
    return out


def _n(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def tenable_scan_report(r, opts=None):
    """site_tenable_scan's result -> roles/site_email's _report."""
    o = opts or {}
    r = r or {}
    plan = r.get("plan") or {}
    targets, scans, problems = list(plan.get("targets") or []), list(plan.get("scans") or []), list(plan.get("problems") or [])
    launched, results = list(r.get("launched") or []), list(r.get("results") or [])
    zones, policies, repos, creds = (list(r.get(k) or []) for k in ("zones", "policies", "repositories", "credentials"))
    check, launch = bool(o.get("check")), bool(r.get("launch"))
    max_rows = int(o.get("max_rows") or 200)
    failed = [x for x in launched if x.get("error")]
    by_result = dict((x.get("result_id"), x) for x in results)
    crit = sum(_n(x.get("critical")) for x in results)
    high = sum(_n(x.get("high")) for x in results)
    bad_scanners = [(z["name"], s) for z in zones for s in z.get("scanners") or [] if str(s.get("status", "")) not in ("working", "")]
    used = set(g.get("zone_id") for g in scans)

    status = "ok"
    head = []
    if r.get("error") and not r.get("ok"):
        status = "critical"
        head.append("not run")
    elif launch:
        if failed:
            status = "critical"
        head.append("%d scan(s) launched in %d zone(s) for %d host(s)" % (len(launched) - len(failed), len(set(x["zone_id"] for x in launched if not x.get("error"))),
                                                                          sum(len(x.get("targets") or []) for x in launched if not x.get("error"))))
        if failed:
            head.append("%d not launched" % len(failed))
        if results:
            head.append("%d critical, %d high" % (crit, high))
            if crit:
                status = "critical" if status != "critical" else status
            elif high and status == "ok":
                status = "warning"
    elif targets:
        head.append("%s %d scan(s) in %d zone(s) for %d host(s)" % ("Check: would launch" if check else "would launch", len(scans), len(scans),
                                                                    sum(len(g.get("targets") or []) for g in scans)))
        if not check and not (o.get("policy") and o.get("repository")):
            head.append("nothing launched: set tenable_scan_policy and tenable_scan_repository (listed below)")
    else:
        head.append("nothing to scan - %d zone(s), %d policies, %d repositories listed; set tenable_scan_target" % (len(zones), len(policies), len(repos)))
    if problems:
        head.append("%d host(s) not scanned" % len(problems))
        if status == "ok":
            status = "warning"
    if r.get("error") and r.get("ok"):
        status = "critical"
        head.append("stopped: %s" % r["error"])

    report = {
        "title": "%s: %s" % (o.get("title") or "Tenable.sc scan", ", ".join(head)),
        "subtitle": "Tenable.sc %s%s - %s%s" % (r.get("url") or "", (" as " + r["user"]) if r.get("user") else "",
                                                ("targets: " + str(o.get("target_label"))) if o.get("target_label") else "no targets",
                                                " - Check: nothing launched" if check else ""),
        "status": status,
        "summary": [{"label": "Hosts", "value": len(targets)},
                    {"label": "Scans launched", "value": len(launched) - len(failed), "status": "ok" if launched and not failed else ("critical" if failed else "")},
                    {"label": "Not scanned", "value": len(problems), "status": "warning" if problems else "ok"}]
                   + ([{"label": "Critical", "value": crit, "status": "critical" if crit else "ok"},
                       {"label": "High", "value": high, "status": "warning" if high else "ok"}] if results else []),
        "sections": [],
        "footer": "Tenable.sc at %s. A scan is launched only with tenable_scan_target set and tenable_scan_policy and tenable_scan_repository "
                  "named; Check launches nothing. The zone of each host: its tenable_zone, else its site (tenable_scan_sites), else the zone "
                  "whose ranges hold its address, else tenable_scan_zone_default." % (r.get("url") or "the credential's URL"),
    }
    secs = report["sections"]
    if r.get("error") and not r.get("ok"):
        secs.append({"title": "Not run", "status": "critical", "lines": [r["error"]]})
        return report
    if launched:
        rows, rs = [], []
        for x in launched:
            res = by_result.get(x.get("result_id")) or {}
            st = x.get("status") or ""
            if res:
                st = "%s - %s%s" % (st, res.get("status") or "", (" (%s)" % res["error"]) if res.get("error") else "")
            row = [x.get("zone") or "-", x.get("name") or "-", x.get("scan_id") or "-", x.get("result_id") or "-", len(x.get("targets") or []), st,
                   x.get("error") or "-"]
            if results:
                row += [_n(res.get("critical")), _n(res.get("high")), _n(res.get("medium")), _n(res.get("low"))]
            rows.append(row)
            rs.append("critical" if x.get("error") else ("critical" if _n(res.get("critical")) else ("warning" if _n(res.get("high")) else "ok")))
        secs.append({"title": "Scans", "status": "critical" if failed else "ok",
                     "text": "One scan per zone, from that zone's scanners, with the policy %s into the repository %s%s. A scan of the same name "
                             "from an earlier run is updated and launched again, not made twice." % (o.get("policy") or "?", o.get("repository") or "?",
                                                                                                    " and the credentials %s" % ", ".join(o.get("credentials") or []) if o.get("credentials") else ""),
                     "columns": ["Zone", "Scan", "Scan id", "Result id", "Hosts", "Status", "Why not"] + (["Critical", "High", "Medium", "Low"] if results else []),
                     "rows": rows, "row_status": rs})
    elif scans and targets:
        secs.append({"title": "What would be launched" if (check or not launch) else "Scans", "status": "info",
                     "lines": ["zone %s: %d host(s) - %s" % (g.get("zone"), len(g.get("targets") or []), ", ".join(g.get("targets") or [])) for g in scans]
                     + ([] if (o.get("policy") and o.get("repository")) else
                        ["Nothing is launched without tenable_scan_policy and tenable_scan_repository: pick them from the lists below."])})
    if targets:
        rows, rs = [], []
        for t in sorted(targets, key=lambda t: (0 if t.get("problem") else 1, t.get("zone") or "", t.get("name") or "")):
            rows.append([t.get("name"), t.get("address") if t.get("address") != t.get("name") else "-", t.get("ip") or "-", t.get("zone") or "-",
                         t.get("how") or "-", t.get("problem") or t.get("note") or "-"])
            rs.append("warning" if t.get("problem") else ("info" if t.get("note") else "ok"))
        secs.append({"title": "The hosts and their zones", "status": "warning" if problems else "ok",
                     "text": "Each host's address as the inventory has it, the IP it resolves to from the execution environment, the zone chosen "
                             "and by which rule - the host's tenable_zone, its site (a group, or the code in its name), its address in the zone's "
                             "ranges, or the default. A host no rule gives a zone is not scanned (amber).",
                     "columns": ["Host", "Address", "IP", "Zone", "Chosen by", "Note"], "rows": rows[:max_rows], "row_status": rs[:max_rows]})
    if results:
        rows, rs = [], []
        for x in launched:
            res = by_result.get(x.get("result_id")) or {}
            for h in res.get("hosts") or []:
                rows.append([x.get("zone"), h.get("name") or "-", h.get("ip"), h.get("critical"), h.get("high"), h.get("medium"), h.get("low"), h.get("total")])
                rs.append("critical" if h.get("critical") else ("warning" if h.get("high") else "ok"))
        if rows:
            order = sorted(range(len(rows)), key=lambda i: (RANK.get(rs[i], 9), -_n(rows[i][3]), -_n(rows[i][4])))
            secs.append({"title": "Findings by host, the worst first", "status": "critical" if crit else ("warning" if high else "ok"),
                         "text": "From Tenable.sc's analysis of each scan's result: the vulnerabilities by severity on each host scanned. The "
                                 "details are in Tenable.sc (Analysis > Vulnerabilities, the scan's result).",
                         "columns": ["Zone", "Host", "IP", "Critical", "High", "Medium", "Low", "Total"],
                         "rows": [rows[i] for i in order][:max_rows], "row_status": [rs[i] for i in order][:max_rows]})
        errs = ["result %s: %s" % (x.get("result_id"), x.get("summary_error") or x.get("error")) for x in results if x.get("summary_error") or x.get("error")]
        if errs:
            secs.append({"title": "Results not read", "status": "warning", "lines": errs})
    if zones:
        rows, rs = [], []
        for z in sorted(zones, key=lambda z: z.get("name", "").lower()):
            sc = z.get("scanners") or []
            bad = [s for s in sc if str(s.get("status", "")) not in ("working", "")]
            rows.append([z.get("name"), ", ".join(z.get("ranges") or []) or "(no range)", ", ".join("%s (%s)" % (s.get("name"), s.get("status")) for s in sc) or "(no scanner)",
                         "used" if z.get("id") in used else "-"])
            rs.append("warning" if bad or not sc else ("ok" if z.get("id") in used else ""))
        secs.append({"title": "Scan zones and their scanners", "status": "warning" if bad_scanners else "ok",
                     "text": "Tenable.sc's scan zones: the IP ranges each covers and the scanners that scan them. A host is scanned from the "
                             "zone whose ranges hold its address, unless its tenable_zone or its site says otherwise. A scanner not working "
                             "(amber) is as Tenable.sc's Scanners page shows it.",
                     "columns": ["Zone", "IP ranges", "Scanners (status)", "This run"], "rows": rows, "row_status": rs})
    if not launched:
        if policies:
            secs.append({"title": "Policies this account may use", "lines": ["%s (id %s)%s" % (p.get("name"), p.get("id"), (" - " + p["description"]) if p.get("description") else "") for p in policies]})
        if repos:
            secs.append({"title": "Repositories", "lines": ["%s (id %s, %s %s)" % (x.get("name"), x.get("id"), x.get("type") or "", x.get("format") or "") for x in repos]})
        if creds:
            secs.append({"title": "Scan credentials", "lines": ["%s (id %s, %s)" % (c.get("name"), c.get("id"), c.get("type") or "") for c in creds]})
    if r.get("error") and r.get("ok"):
        secs.append({"title": "Stopped", "status": "critical", "lines": [r["error"]]})
    return report


class FilterModule(object):
    def filters(self):
        return {"tenable_scan_targets": tenable_scan_targets, "tenable_scan_report": tenable_scan_report}
