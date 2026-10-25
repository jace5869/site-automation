# -*- coding: utf-8 -*-
"""Filters for the MID Server part of the ServiceNow health check (roles/servicenow/tasks/health.yml).
Input: what the instance says (ecc_agent, the ecc_queue counts, ecc_agent_issue) and what each MID
Server host said (roles/servicenow/files/mid_host.ps1). Output: findings in the site_findings shape
(check servicenow), the MID Server table for the report, and one status line.

What counts:
  critical  a MID Server not Up (Down, or any state but Up / Upgrading / Paused), or not validated; Up but
            its heartbeat older than stale_minutes; one of servicenow_mid_expected missing; queue_critical
            output messages stuck; on the host: the MID service not running, its agent log not written for
            log_stale_minutes while it runs (hung), config.xml pointing at another instance, the agent
            drive under disk_critical % free, a MID host not reached
  warning   Upgrading or Paused; MID Servers on different versions; queue_warn stuck messages; open MID
            Server Issues; on the host: the service not set to start by itself, severe_warn SEVERE lines,
            jvm_warn JVM restarts, the agent drive under disk_warn % free, no config.xml, no MID service
            at all on a host of the group
Nothing here changes anything."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import fnmatch
import re

OK_STATES = ("up",)
WARN_STATES = ("upgrading", "paused")


def _s(v):
    if isinstance(v, dict):            # a reference field read with sysparm_display_value
        v = v.get("display_value", v.get("value", ""))
    return " ".join(str(v if v is not None else "").split())


def _epoch(ts):
    """'2026-10-24 10:00:00' (the Table API's UTC value) -> seconds since 1970, or None."""
    t = _s(ts)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(t, fmt).replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _short(name):
    n = _s(name).lower()
    return n.split(".")[0] if n and not re.match(r"^\d+\.\d+\.\d+\.\d+$", n) else n


def _host_of_url(url):
    u = _s(url).lower()
    u = re.sub(r"^[a-z]+://", "", u)
    return u.split("/")[0].split(":")[0]


def _wanted(name, patterns):
    pats = [str(p).lower() for p in patterns or [] if str(p).strip()]
    return not pats or any(fnmatch.fnmatchcase(_s(name).lower(), p) for p in pats)


def sn_mid_agents(rows, opts=None):
    """The ecc_agent rows -> one dict per MID Server (only opts.names, * and ?), each with its heartbeat's
    age in minutes (opts.now: seconds since 1970)."""
    o = opts or {}
    now = float(o.get("now") or 0)
    out = []
    for r in rows or []:
        name = _s(r.get("name"))
        if not name or not _wanted(name, o.get("names")):
            continue
        last = _epoch(r.get("last_refreshed"))
        out.append({"name": name, "status": _s(r.get("status")), "validated": _s(r.get("validated")).lower() == "true",
                    "host_name": _s(r.get("host_name")), "ip": _s(r.get("ip_address")), "version": _s(r.get("version")),
                    "last_refreshed": _s(r.get("last_refreshed")),
                    "age_min": round((now - last) / 60.0, 1) if last is not None and now else None})
    return sorted(out, key=lambda a: a["name"].lower())


def sn_mid_queue(rows):
    """The ecc_queue counts (the Aggregate API, grouped by agent) -> {MID Server name: stuck messages}.
    The agent field is 'mid.server.<name>'."""
    out = {}
    for r in rows or []:
        agent = ""
        for g in r.get("groupby_fields") or []:
            if g.get("field") == "agent":
                agent = _s(g.get("value"))
        name = re.sub(r"^mid\.server\.", "", agent, flags=re.I)
        try:
            n = int(str((r.get("stats") or {}).get("count") or 0))
        except ValueError:
            n = 0
        if name and n:
            out[name] = out.get(name, 0) + n
    return out


def _host_for(agent, hosts):
    """The MID host result that runs this MID Server: the one whose config.xml names it, else the one
    whose inventory name or Windows name is the agent's host name."""
    for h in hosts or []:
        for m in h.get("mids") or []:
            if m.get("name") and m["name"].lower() == agent["name"].lower():
                return h, m
    for h in hosts or []:
        if agent.get("host_name") and _short(agent["host_name"]) in (_short(h.get("host")), _short(h.get("hostname"))):
            return h, None
    return None, None


def sn_mid_findings(agents, queue=None, issues=None, hosts=None, opts=None):
    """Every MID Server problem as a finding {check, id, severity, summary, hint}."""
    o, q, f = opts or {}, queue or {}, []
    stale = float(o.get("stale_minutes") or 15)

    def add(fid, sev, summary, hint=""):
        f.append({"check": "servicenow", "id": fid, "severity": sev, "summary": summary, "hint": hint})

    for a in agents or []:
        h, m = _host_for(a, hosts)
        where = a["host_name"] or "?"
        st = a["status"].lower()
        svc = ""
        if m and m.get("state") and m["state"].lower() != "running":
            svc = "; its service %s is %s on %s" % (m.get("service"), m.get("state"), h.get("host"))
        hint = ("on %s: Get-Service %s; Get-Content '%s\\logs\\agent0.log.0' -Tail 50" % (
            (h or {}).get("host") or where, (m or {}).get("service") or "snc_mid*", (m or {}).get("agent_dir") or "<agent>"))
        if st not in OK_STATES + WARN_STATES or not a["validated"]:
            add("servicenow:mid:" + a["name"], "critical",
                "MID Server %s on %s is %s%s (last seen %s)%s" % (a["name"], where, a["status"] or "?",
                                                                 " and NOT validated" if not a["validated"] else "",
                                                                 a["last_refreshed"] or "?", svc), hint)
        elif st in WARN_STATES:
            add("servicenow:mid:" + a["name"], "warning",
                "MID Server %s on %s is %s (last seen %s)" % (a["name"], where, a["status"], a["last_refreshed"] or "?"), hint)
        elif a["age_min"] is not None and a["age_min"] > stale:
            add("servicenow:mid-stale:" + a["name"], "critical",
                "MID Server %s on %s says Up, but has not reported for %d minutes (last %s UTC; more than %d)%s" % (
                    a["name"], where, int(a["age_min"]), a["last_refreshed"], int(stale), svc), hint)
        n = q.get(a["name"], 0)
        if n >= int(o.get("queue_warn") or 1):
            add("servicenow:mid-queue:" + a["name"], "critical" if n >= int(o.get("queue_critical") or 50) else "warning",
                "%d output message(s) have waited more than %s minutes for MID Server %s: it is not picking up its work "
                "(discovery, orchestration and integrations through it are stalled)" % (n, o.get("queue_minutes", 10), a["name"]),
                "ServiceNow: ECC > Queue, filter Agent = mid.server.%s, Queue = output, State = ready" % a["name"])
    names = {a["name"].lower() for a in agents or []}
    for e in o.get("expected") or []:
        if _s(e) and _s(e).lower() not in names:
            add("servicenow:mid-missing:" + _s(e), "critical",
                "MID Server %s (servicenow_mid_expected) is not in the instance's MID Server list" % _s(e),
                "ServiceNow: MID Server > Servers; was it renamed, or its record deleted?")
    if o.get("version_check", True):
        vers = sorted({a["version"] for a in agents or [] if a["version"]})
        if len(vers) > 1:
            add("servicenow:mid-versions", "warning",
                "the MID Servers run %d different versions (%s): one may be stuck in its upgrade" % (
                    len(vers), "; ".join("%s: %s" % (v, ", ".join(a["name"] for a in agents if a["version"] == v)) for v in vers)),
                "ServiceNow: MID Server > Servers, the Version column; on the host: agent\\logs\\agent0.log.0 (upgrade)")
    by_mid = {}
    for i in issues or []:
        if _s(i.get("state")).lower() in ("resolved", "closed", "ignored"):
            continue
        by_mid.setdefault(_s(i.get("mid_server")) or "?", []).append(i)
    for name in sorted(by_mid):
        if name != "?" and not _wanted(name, o.get("names")):
            continue
        rows = by_mid[name]
        add("servicenow:mid-issues:" + name, "warning",
            "MID Server %s has %d open MID Server Issue(s): %s" % (
                name, len(rows), "; ".join(_s(i.get("message"))[:160] for i in rows[:3]) + (" ..." if len(rows) > 3 else "")),
            "ServiceNow: MID Server > Issues")
    f += sn_mid_host_findings(hosts, o)
    return f


def sn_mid_host_findings(hosts, opts=None):
    """What each MID Server host said (mid_host.ps1, or not reached) -> findings."""
    o, f = opts or {}, []
    hours = o.get("hours", 24)
    inst = _host_of_url(o.get("instance_url"))

    def add(fid, sev, summary, hint=""):
        f.append({"check": "servicenow", "id": fid, "severity": sev, "summary": summary, "hint": hint})

    for h in hosts or []:
        host = h.get("host") or "?"
        if not h.get("reachable", True):
            add("servicenow:mid-host:%s:unreachable" % host, "critical",
                "MID Server host %s could not be reached over WinRM: %s" % (host, _s(h.get("error"))[:200] or "unreachable"),
                "Test-WSMan %s; the Machine (Windows) credential on the ServiceNow health template" % host)
            continue
        if h.get("error"):
            add("servicenow:mid-host:%s:check-error" % host, "warning",
                "the MID Server check could not run on %s: %s" % (host, _s(h.get("error"))[:200]),
                "run playbooks/win_connection_test.yml against %s" % host)
            continue
        mids = h.get("mids") or []
        if not mids:
            add("servicenow:mid-host:%s:no-service" % host, "warning",
                "no MID Server service on %s (none named %s or \"ServiceNow MID Server*\")" % (host, o.get("service_match", "snc_mid*")),
                "Get-CimInstance Win32_Service | Where-Object DisplayName -like 'ServiceNow*' | Format-Table Name, State, PathName")
        for m in mids:
            svc = m.get("service") or "?"
            who = "MID Server %s (service %s) on %s" % (m.get("name") or "?", svc, host)
            base = "servicenow:mid-host:%s:%s:" % (host, svc)
            logdir = (m.get("agent_dir") or "<agent>") + "\\logs"
            running = (m.get("state") or "").lower() == "running"
            if not running:
                add(base + "stopped", "critical", "%s is %s" % (who, m.get("state") or "not running"),
                    "Get-Service %s; Get-Content '%s\\wrapper.log' -Tail 50; Start-Service %s" % (svc, logdir, svc))
            if (m.get("start_mode") or "").lower() not in ("auto", "automatic", ""):
                add(base + "start-mode", "warning",
                    "%s does not start by itself (start mode %s): a restart of the server leaves it down" % (who, m.get("start_mode")),
                    "Set-Service %s -StartupType Automatic" % svc)
            if m.get("error"):
                add(base + "read-error", "warning", "%s: %s" % (who, _s(m["error"])[:200]), "dir '%s'" % m.get("agent_dir"))
            if not m.get("agent_dir"):
                add(base + "no-agent", "warning", "%s: its agent folder could not be read from the service's command line" % who,
                    "Get-CimInstance Win32_Service -Filter \"Name='%s'\" | Format-List PathName" % svc)
                continue
            if not m.get("config"):
                add(base + "no-config", "warning", "%s: no config.xml in %s" % (who, m.get("agent_dir")), "dir '%s'" % m.get("agent_dir"))
            elif o.get("url_check", True) and inst and m.get("url") and _host_of_url(m["url"]) != inst:
                add(base + "url", "critical",
                    "%s points at %s, not at this instance (%s): after a clone or a move it reports to the wrong instance" % (
                        who, _host_of_url(m["url"]), inst),
                    "Select-String -Path '%s\\config.xml' -Pattern 'name=\"url\"'" % m.get("agent_dir"))
            log = m.get("log") or {}
            if running and log.get("exists") and log.get("age_min") is not None and float(log["age_min"]) > float(o.get("log_stale_minutes") or 30):
                add(base + "log-stale", "critical",
                    "%s runs, but its agent log has not been written for %d minutes: the MID Server may be hung" % (who, int(float(log["age_min"]))),
                    "Get-Item '%s\\agent0.log.0' | Format-List LastWriteTime; Get-Content '%s\\wrapper.log' -Tail 50; Restart-Service %s" % (logdir, logdir, svc))
            elif running and not log.get("exists"):
                add(base + "no-log", "warning", "%s: no agent log at %s\\agent0.log.0" % (who, logdir), "dir '%s'" % logdir)
            if int(log.get("count") or 0) >= int(o.get("severe_warn") or 20):
                add(base + "severe", "warning",
                    "%s logged %d SEVERE line(s) in the last %s h; the latest: %s" % (who, int(log["count"]), hours, (log.get("last") or [""])[-1][:200]),
                    "Select-String -Path '%s\\agent0.log.0' -Pattern SEVERE | Select-Object -Last 20" % logdir)
            wr = m.get("wrapper") or {}
            if int(wr.get("count") or 0) >= int(o.get("jvm_warn") or 1):
                add(base + "jvm", "warning",
                    "%s: the JVM crashed, hung or ran out of memory %d time(s) in the last %s h (wrapper.log); the latest: %s" % (
                        who, int(wr["count"]), hours, (wr.get("last") or [""])[-1][:200]),
                    "Select-String -Path '%s\\wrapper.log' -Pattern 'JVM|OutOfMemory' | Select-Object -Last 20; "
                    "the memory: wrapper.java.maxmemory in agent\\conf\\wrapper-override.conf" % logdir)
            d = m.get("disk") or {}
            if d.get("free_pct") is not None:
                pct = float(d["free_pct"])
                if pct < float(o.get("disk_critical") or 5) or pct < float(o.get("disk_warn") or 10):
                    add(base + "disk", "critical" if pct < float(o.get("disk_critical") or 5) else "warning",
                        "%s: drive %s has %s%% free (%s GB): the MID Server stops when its logs and work files cannot be written" % (
                            who, d.get("drive"), d["free_pct"], d.get("free_gb")),
                        "Get-ChildItem '%s' | Sort-Object Length -Descending | Select-Object -First 10" % logdir)
    return f


def sn_mid_section(agents, queue=None, hosts=None, opts=None):
    """The MID Server table of the report (the site_email layout): one row per MID Server the instance
    knows, then the host-side services no instance record matched."""
    q, o = queue or {}, opts or {}
    rows, rs, used = [], [], set()
    for a in agents or []:
        h, m = _host_for(a, hosts)
        if m:
            used.add((h.get("host"), m.get("service")))
        st = a["status"].lower()
        bad = st not in OK_STATES + WARN_STATES or not a["validated"] or (
            a["age_min"] is not None and a["age_min"] > float(o.get("stale_minutes") or 15)) or (m and (m.get("state") or "").lower() != "running")
        warn = st in WARN_STATES or q.get(a["name"], 0) > 0
        rows.append([a["name"], a["host_name"] or "-", a["status"] or "?", "yes" if a["validated"] else "NO", a["version"] or "-",
                     "%s min ago" % int(a["age_min"]) if a["age_min"] is not None else "-", str(q.get(a["name"], 0)) if a["name"] in q else "0",
                     ("%s %s" % (m.get("service"), m.get("state"))) if m else ("not checked" if not h else "no service matched")])
        rs.append("critical" if bad else "warning" if warn else "")
    for h in hosts or []:
        for m in h.get("mids") or []:
            if (h.get("host"), m.get("service")) not in used:
                rows.append([m.get("name") or "?", h.get("host") or "?", "not in the instance's list", "-", "-", "-", "-",
                             "%s %s" % (m.get("service"), m.get("state"))])
                rs.append("warning")
        if not h.get("reachable", True):
            rows.append(["-", h.get("host") or "?", "-", "-", "-", "-", "-", "NOT REACHED"])
            rs.append("critical")
    return {"title": "MID Servers", "columns": ["MID Server", "Host", "Status", "Validated", "Version", "Heartbeat",
                                                "Stuck output", "Service on the host"],
            "rows": rows, "row_status": rs}


def sn_mid_status(agents, queue=None, hosts=None):
    """One line for the job's output."""
    if not agents and not hosts:
        return "MID Servers: none"
    parts = ["%s %s%s" % (a["name"], a["status"] or "?", "" if a["validated"] else " (NOT validated)") for a in agents or []]
    hs = hosts or []
    return "MID Servers: %s%s" % (", ".join(parts) or "none in the instance's list",
                                  "; hosts checked: %d of %d" % (sum(1 for h in hs if h.get("reachable", True) and not h.get("error")), len(hs))
                                  if hs else "")


class FilterModule(object):
    def filters(self):
        return {"sn_mid_agents": sn_mid_agents, "sn_mid_queue": sn_mid_queue, "sn_mid_findings": sn_mid_findings,
                "sn_mid_host_findings": sn_mid_host_findings, "sn_mid_section": sn_mid_section, "sn_mid_status": sn_mid_status}
