# -*- coding: utf-8 -*-
"""The availability checks (roles/api_availability: playbooks/stigman_availability.yml, servicenow_availability.yml):
what the probes of site_http_probe say - availability_judge (the findings and the status), availability_line (the one
line of a run) and availability_sections (the report's tables)."""


def _avail_pct(p):
    return "-" if p is None else ("%s%%" % (("%.2f" % p).rstrip("0").rstrip(".")))


def _avail_period(hours):
    hours = int(hours)
    return {24: "Last 24 hours", 168: "Last 7 days", 720: "Last 30 days"}.get(hours, "Last %d hours" % hours)


def availability_judge(probes, opts=None):
    """{status: up / degraded / down, findings: [site_findings entries]} of a run's probes.
    A probe of the group service (the address people use) that never answered well: down, critical. A node (one
    server behind it) that never answered well: opts.node_down severity (warning) - every node: down, critical, when
    there is no service probe. Some attempts bad: flaky, warning. The median answer slower than opts.slow_ms: warning.
    A probe of the group dependency (what the service needs: its login server) is judged as a service probe, but is
    not the service's address.
    opts: {service: the name in the summaries, check: the findings' check, slow_ms, node_down, hint: {probe name: a
    command to look with}}"""
    o = dict(opts or {})
    service, check = o.get("service", "the service"), o.get("check", "availability")
    slow, node_down, hints = int(o.get("slow_ms", 3000)), o.get("node_down", "warning"), o.get("hint") or {}
    findings = []

    def add(sev, key, summary, name):
        findings.append({"check": check, "id": "%s:%s" % (check, key), "severity": sev, "summary": summary,
                         "hint": hints.get(name, "")})

    probes = probes or []
    svc = [p for p in probes if p.get("group", "service") == "service"]
    nodes = [p for p in probes if p.get("group") == "node"]
    deps = [p for p in probes if p.get("group") == "dependency"]
    for p in probes:
        n, total, name = int(p.get("good", 0)), len(p.get("attempts") or []), p.get("name", "")
        is_node = p.get("group") == "node"
        what = ("%s node %s" % (service, name)) if is_node else ("%s (%s)" % (service, name))
        if p.get("group") == "dependency":
            what = "%s, which %s needs," % (name, service)
        if n == 0:
            sev = node_down if is_node else "critical"
            add(sev, "down:" + name, "%s does not answer: %s (%d of %d tries)" % (what, p.get("why") or "no answer", total, total), name)
            continue
        if n < total:
            add("warning", "flaky:" + name, "%s answered %d of %d tries; the others: %s" % (what, n, total, p.get("why") or "?"), name)
        med = p.get("ms_median")
        if med is not None and int(med) > slow:
            add("warning", "slow:" + name, "%s is slow: %d ms (median of %d tries; warning above %d ms)" % (what, int(med), total, slow), name)
    if nodes and not svc and all(int(p.get("good", 0)) == 0 for p in nodes):
        add("critical", "down", "%s does not answer on any of its %d nodes" % (service, len(nodes)), "")
    sevs = {f["severity"] for f in findings}
    down = any(int(p.get("good", 0)) == 0 for p in svc + deps) or (nodes and not svc and all(int(p.get("good", 0)) == 0 for p in nodes))
    status = "down" if down or "critical" in sevs else ("degraded" if findings else "up")
    return {"status": status, "findings": findings}


def availability_line(probes, judged, history=None, service="the service"):
    """One line for the job's output: UP / DEGRADED / DOWN, each probe's tries and median, the last 24 hours, and how
    long it has been down (or that it is back)."""
    status = (judged or {}).get("status", "up")
    parts = ["%s %s" % (service, status.upper())]
    for p in probes or []:
        med = p.get("ms_median")
        parts.append("%s %d/%d%s" % (p.get("name", ""), int(p.get("good", 0)), len(p.get("attempts") or []),
                                     "" if med is None else " %d ms" % int(med)))
    h = history or {}
    if h.get("ok"):
        day = [x for x in h.get("periods") or [] if int(x.get("hours", 0)) == 24]
        if day:
            parts.append("last 24 h %s" % _avail_pct(day[0].get("pct")))
        prev = (h.get("previous") or {}).get("status")
        if status != "down" and prev == "failed":
            parts.append("back since the last run (it failed)")
        if status == "down" and (h.get("last_up") or {}).get("finished"):
            parts.append("last good run %s" % h["last_up"]["finished"])
    line = " - ".join(parts[:1]) + (": " + "; ".join(parts[1:]) if len(parts) > 1 else "")
    found = [f.get("summary", "") for f in (judged or {}).get("findings") or []]
    return line + (" - " + "; ".join(found) if found else "")


def availability_sections(probes, history=None, opts=None):
    """The report's tables: each probe (the address, its tries, the median and slowest answers, what was wrong), and the
    availability over a day, a week and a month from the template's past runs (site_availability_history)."""
    o = dict(opts or {})
    slow, node_down = int(o.get("slow_ms", 3000)), o.get("node_down", "warning")
    rows, rs = [], []
    for p in probes or []:
        n, total = int(p.get("good", 0)), len(p.get("attempts") or [])
        med, mx = p.get("ms_median"), p.get("ms_max")
        if n == 0:
            st, res = (node_down if p.get("group") == "node" else "critical"), "DOWN"
        elif n < total or (med is not None and int(med) > slow):
            st, res = "warning", ("FLAKY" if n < total else "SLOW")
        else:
            st, res = "", "UP"
        codes = sorted({str(a.get("status")) if int(a.get("status", -1)) > 0 else "none" for a in p.get("attempts") or []})
        rows.append([p.get("name", ""), p.get("url", ""), p.get("group") or "service", res,
                     "%d of %d" % (n, total), "-" if med is None else "%d ms" % int(med), "-" if mx is None else "%d ms" % int(mx),
                     ", ".join(codes), p.get("why") or ""])
        rs.append(st)
    sec = {"title": "What answered", "columns": ["Probe", "Address", "Kind", "Result", "Good tries", "Median", "Slowest", "HTTP", "Why not"],
           "rows": rows, "row_status": rs,
           "text": "Each address asked several times, a moment apart. Slow = the median above %d ms. HTTP none = no answer at all "
                   "(the name, a firewall, the certificate, a timeout)." % slow}
    st = "critical" if "critical" in rs else ("warning" if "warning" in rs else "")
    if st:
        sec["status"] = st
    out = [sec]
    h = history or {}
    if h.get("ok"):
        prows = [[_avail_period(x.get("hours", 0)), _avail_pct(x.get("pct")), str(x.get("up", 0)), str(x.get("down", 0))]
                 for x in h.get("periods") or []]
        text = "Runs of this job that ended well (the service answered) against those that failed; a canceled run is not counted."
        if (h.get("down_since") or {}).get("started"):
            text += " Down since the run of %s (job %s)." % (h["down_since"]["started"], h["down_since"].get("id"))
        out.append({"title": "Availability", "columns": ["Period", "Available", "Runs up", "Runs down"], "rows": prows, "text": text})
    elif h.get("why"):
        out.append({"title": "Availability", "text": "Not counted: %s." % str(h["why"]).rstrip(".")})
    return out


class FilterModule(object):
    def filters(self):
        return {"availability_judge": availability_judge, "availability_line": availability_line,
                "availability_sections": availability_sections}
