# -*- coding: utf-8 -*-
"""Filters for the VMware health report (roles/vmware_vm/tasks/health.yml). Input: what
roles/vmware_vm/library/site_vmware_health.py returns. Output: what is wrong (vm_health_problems)
and the report in the layout roles/site_email renders (vm_health_report: title, summary, sections
with columns / rows, and per row row_status and cell_status for the colours).

What counts as a problem:
  critical  a host not connected to vCenter (or powered off); the HA agent of a host isolated,
            partitioned, unreachable or in error; vCLS retreat mode on a cluster with DRS (DRS stops);
            a red hardware sensor; a red alarm; CPU or memory at or above the critical percentage; a host's
            clock off from vCenter's by vm_health_clock_crit_seconds or more
  warning   HA or DRS off on a cluster of 2 or more hosts (vm_health_require_ha / _drs); HA host
            monitoring off; HA failover capacity below what admission control reserves; DRS
            recommendations waiting or DRS faults; retreat mode without DRS; a yellow sensor; a host
            that needs a restart; a configuration issue; a yellow alarm; CPU or memory at or above the
            warning percentage; a host's clock off from vCenter's by vm_health_clock_warn_seconds or more, no
            NTP server set on it or its NTP service stopped; vCenter's clock off from the AAP job's
  info      a host in maintenance mode or standby; HA admission control off; DRS in manual mode
Times: UTC (as vCenter, ESXi and their log files keep them), and also in report_time_zone when it is set
(the vSphere Client shows the browser's time zone)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime

try:
    import zoneinfo
except ImportError:                             # Python before 3.9: UTC only
    zoneinfo = None

RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4, "": 5}
ALARM_SEV = {"red": "critical", "yellow": "warning"}
STATUS_SEV = {"red": "critical", "yellow": "warning"}
HA_LABEL = {"master": "primary", "connectedToMaster": "secondary", "election": "electing a primary",
            "uninitialized": "not configured", "initializationError": "configuration error",
            "uninitializationError": "unconfiguration error", "fdmUnreachable": "agent unreachable",
            "networkPartitionedFromMaster": "network partitioned", "networkIsolated": "network isolated",
            "hostDown": "host down"}
HA_SEV = {"master": "ok", "connectedToMaster": "ok", "election": "warning", "uninitialized": "warning",
          "initializationError": "critical", "uninitializationError": "critical", "fdmUnreachable": "critical",
          "networkPartitionedFromMaster": "critical", "networkIsolated": "critical", "hostDown": "critical"}
HA_FIX = {
    "election": "Usually over in a minute. If it stays: host > Reconfigure for vSphere HA.",
    "uninitialized": "Host > Reconfigure for vSphere HA.",
    "initializationError": "Host > Reconfigure for vSphere HA; if it fails again, check the host's management network and DNS.",
    "uninitializationError": "Host > Reconfigure for vSphere HA.",
    "fdmUnreachable": "vCenter cannot reach the HA agent: check the host's management network, then Reconfigure for vSphere HA.",
    "networkPartitionedFromMaster": "The management network is split: the hosts of the cluster cannot all reach each other. "
                                    "Check the management VLAN and uplinks between them.",
    "networkIsolated": "The host cannot reach the other hosts or its isolation address: check its management network "
                       "and uplinks. HA may restart its VMs elsewhere.",
    "hostDown": "The host is down or not responding: see its connection.",
}
CONN_LABEL = {"notResponding": "not responding"}
DRS_LABEL = {"fullyAutomated": "fully automated", "partiallyAutomated": "partially automated", "manual": "manual"}


def _t(iso):
    return str(iso or "").replace("T", " ").replace("Z", "")


def _zone(name):
    """report_time_zone -> (its tzinfo, or None for UTC only; a note when it is not a time zone known here)."""
    name = str(name or "").strip()
    if not name or name.upper() in ("UTC", "Z", "ETC/UTC", "GMT"):
        return None, ""
    try:
        return zoneinfo.ZoneInfo(name), ""
    except Exception:                           # noqa: BLE001 - unknown name, no time zone data in the EE
        return None, ("report_time_zone %s is not a time zone this execution environment knows "
                      "(a name as timedatectl list-timezones prints them, e.g. America/New_York): times are in UTC" % name)


def _when(iso, tz=None):
    """'2026-10-25T14:03:12Z' -> '2026-10-25 14:03:12 UTC'; with a time zone (_zone) its time first, then UTC's:
    '2026-10-25 10:03:12 EDT (14:03 UTC)' - summer time as it was on that day."""
    s = _t(iso)
    if not s:
        return ""
    try:
        u = datetime.datetime.strptime(str(iso).strip().rstrip("Z")[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return s
    if tz is None:
        return u.strftime("%Y-%m-%d %H:%M:%S UTC")
    lt = u.astimezone(tz)
    return "%s (%s UTC)" % (lt.strftime("%Y-%m-%d %H:%M:%S %Z"), u.strftime("%H:%M" if lt.date() == u.date() else "%Y-%m-%d %H:%M"))


def _secs(x):
    """A clock difference in words: 'ahead by 2 min 5 s', 'behind by 40 s'."""
    a = abs(float(x))
    m, sec = divmod(int(round(a)), 60)
    h, m = divmod(m, 60)
    txt = " ".join(p for p in ("%d h" % h if h else "", "%d min" % m if m else "", "%d s" % sec if sec or not (h or m) else "") if p)
    return ("ahead by " if x > 0 else "behind by ") + txt


def _clock_cell(x):
    """A host's clock against vCenter's for the table: '+2 s', '-5 min 3 s', '?' (not read)."""
    if x is None:
        return "?"
    return ("+" if x >= 0 else "-") + _secs(x).split(" by ", 1)[1]


def _worst(statuses):
    s = sorted((x for x in statuses if x), key=lambda x: RANK.get(x, 9))
    return s[0] if s else ""


def _short(vc):
    vc = str(vc or "")
    host = vc.split(":")[0]
    return vc if host.replace(".", "").isdigit() else host.split(".")[0] + vc[len(host):]


def _vcs(d):
    st = d.get("vcenters")
    if st:
        return [x.get("name") for x in st if x.get("ok") and not str(x.get("note", "")).startswith("the same vCenter")]
    return [d["vcenter"]] if d.get("vcenter") else []


def _pct(used, total):
    try:
        used, total = float(used), float(total)
    except (TypeError, ValueError):
        return None
    return round(100.0 * used / total, 1) if total > 0 else None


def _usage_sev(pct, warn, crit):
    if pct is None:
        return ""
    if crit and pct >= crit:
        return "critical"
    if warn and pct >= warn:
        return "warning"
    return ""


def _pct_text(p):
    return "-" if p is None else ("%d%%" % round(p))


def _opts(o):
    o = o or {}
    if "cpu_warn" in o:                         # already read
        return o
    tz, tz_note = _zone(o.get("time_zone"))
    return {"require_ha": bool(o.get("require_ha", True)), "require_drs": bool(o.get("require_drs", True)),
            "clock_warn": float(o.get("clock_warn_seconds", 60) or 0), "clock_crit": float(o.get("clock_crit_seconds", 300) or 0),
            "tz": tz, "tz_note": tz_note,
            "cpu_warn": float(o.get("cpu_warn_pct", 85) or 0), "cpu_crit": float(o.get("cpu_crit_pct", 95) or 0),
            "mem_warn": float(o.get("mem_warn_pct", 90) or 0), "mem_crit": float(o.get("mem_crit_pct", 95) or 0),
            "max_rows": int(o.get("max_rows", 500) or 0), "datacenter": o.get("datacenter") or ""}


def _up(h):
    """A host that runs VMs now: connected, powered on, not in maintenance or standby."""
    return (h.get("connection") == "connected" and h.get("power", "poweredOn") in ("poweredOn", "")
            and not h.get("maintenance") and h.get("standby", "none") in ("none", "", "exiting"))


def _alarm_problems(alarms, what, tz=None):
    out = []
    for a in alarms or []:
        sev = ALARM_SEV.get(a.get("status"))
        if sev:
            out.append((sev, "Alarm: %s%s (since %s)" % (a.get("alarm", ""), ", acknowledged" if a.get("acknowledged") else "",
                                                        _when(a.get("time"), tz)),
                        "Monitor > Issues and Alarms > Triggered Alarms on the %s: the alarm says what to look at." % what))
    return out


def _status_problem(status, problems, what):
    """vCenter's own red / yellow when nothing listed explains it."""
    sev = STATUS_SEV.get(status)
    if sev and not any(p[0] == sev or (sev == "warning" and p[0] == "critical") for p in problems):
        return [(sev, "vCenter shows the %s %s" % (what, status), "Look at its Summary and Monitor > Issues and Alarms in vCenter.")]
    return []


def host_problems(h, cluster, o):
    """[(severity, problem, what to do)] for one host. cluster: its cluster record or None."""
    o = _opts(o)
    out = []
    conn, power = h.get("connection", ""), h.get("power", "")
    since = (" since %s (vCenter's event)" % _when(h["down_since"], o["tz"])) if h.get("down_since") else ""
    if conn == "notResponding":
        out.append(("critical", "Not responding to vCenter" + since,
                    "Check the host is on and its management network; then right-click > Connection > Connect."))
    elif conn == "disconnected":
        out.append(("critical", "Disconnected from vCenter" + since,
                    "Right-click the host > Connection > Connect (someone may have disconnected it)."))
    elif conn and conn != "connected":
        out.append(("critical", "Connection: %s%s" % (conn, since), "Check the host and its management network."))
    if conn == "connected":
        if power == "poweredOff":
            out.append(("critical", "Powered off", "Power it on (from its management controller), or remove it from the cluster."))
        if h.get("standby") in ("in", "entering") or power == "standBy":
            out.append(("info", "In standby (DPM powered it down)", "Nothing to do: DRS powers it on when the cluster needs it."))
    if h.get("maintenance"):
        out.append(("info", "In maintenance mode", "Exit maintenance mode when the work on it is done."))
    ha_on = bool(cluster and (cluster.get("ha") or {}).get("enabled"))
    st = h.get("ha_state") or ""
    if ha_on and _up(h) and st and HA_SEV.get(st, "warning") != "ok":
        out.append((HA_SEV.get(st, "warning"), "vSphere HA agent: %s" % HA_LABEL.get(st, st),
                    HA_FIX.get(st, "Host > Reconfigure for vSphere HA.")))
    bad = h.get("sensors_bad") or []
    for sev, col in (("critical", "red"), ("warning", "yellow")):
        names = [s.get("name", "") for s in bad if s.get("state") == col]
        if names:
            out.append((sev, "Hardware %s: %s%s" % ("fault" if col == "red" else "warning", ", ".join(names[:5]),
                                                   " and %d more" % (len(names) - 5) if len(names) > 5 else ""),
                        "Host > Monitor > Hardware Health, and the server's management controller (iDRAC, iLO, XCC)."))
    if h.get("reboot_required") and conn == "connected":
        out.append(("warning", "Needs a restart (an update or a setting waits for it)",
                    "Restart it in a maintenance window (maintenance mode first)."))
    for i in h.get("config_issues") or []:
        out.append(("warning", i, "Host > Summary in vCenter (the issue is shown there)."))
    out += _clock_problems(h, o)
    out += _alarm_problems(h.get("alarms"), "host", o["tz"])
    if _up(h):
        cpu = _pct(h.get("cpu_used_mhz"), h.get("cpu_mhz"))
        mem = _pct(h.get("mem_used_mb"), h.get("mem_mb"))
        for label, p, w, c in (("CPU", cpu, o["cpu_warn"], o["cpu_crit"]), ("Memory", mem, o["mem_warn"], o["mem_crit"])):
            sev = _usage_sev(p, w, c)
            if sev:
                out.append((sev, "%s %s used" % (label, _pct_text(p)),
                            "DRS should spread the load; if it cannot, move or power off VMs (see the capacity report)."))
    out += _status_problem(h.get("status"), out, "host")
    return out


def _clock_problems(h, o):
    """A connected host's clock against vCenter's, and its NTP setting (nothing said when vCenter would not say)."""
    if h.get("connection") != "connected":
        return []
    out = []
    x = h.get("clock_offset_s")
    sev = ("critical" if o["clock_crit"] and abs(x) >= o["clock_crit"] else "warning" if o["clock_warn"] and abs(x) >= o["clock_warn"]
           else "") if x is not None else ""
    if sev:
        out.append((sev, "Clock %s against vCenter's: its log times (hostd, vmkernel) differ from vCenter's events by as much" % _secs(x),
                    "Host > Configure > System > Time Configuration: NTP servers set and the NTP service running"
                    + ("; Active Directory logins fail from 5 minutes off" if abs(x) >= 300 else "") + "."))
    servers = h.get("ntp_servers")
    if servers is not None and not servers and h.get("clock_protocol") != "ptp":
        out.append(("warning", "No NTP server set: its clock drifts, and its log times stop matching vCenter's events",
                    "Host > Configure > System > Time Configuration > Network Time Protocol: your NTP servers, the service started "
                    "(Start and stop with host)."))
    elif servers and h.get("ntp_running") is False and h.get("clock_protocol") != "ptp":
        out.append(("warning", "NTP service not running (servers %s): its clock drifts" % ", ".join(servers[:3]),
                    "Host > Configure > System > Services > NTP Daemon: Start, policy Start and stop with host."))
    return out


def cluster_problems(c, hosts, o):
    """[(severity, problem, what to do)] for one cluster (its own: HA, DRS, vCLS, issues, alarms,
    usage). hosts: its hosts."""
    o = _opts(o)
    out = []
    ha, drs = c.get("ha") or {}, c.get("drs") or {}
    n = len(hosts) or int(c.get("hosts") or 0)
    if not ha.get("enabled"):
        if o["require_ha"] and n >= 2:
            out.append(("warning", "vSphere HA is off: the VMs of a failed host are not restarted",
                        "Cluster > Configure > vSphere Availability > Edit: turn on vSphere HA (vm_health_require_ha: false if this is on purpose)."))
    else:
        if ha.get("host_monitoring") == "disabled":
            out.append(("warning", "vSphere HA host monitoring is off: a failed host is not noticed",
                        "Cluster > Configure > vSphere Availability > Edit > Failures and responses: Enable Host Monitoring."))
        adm = ha.get("admission")
        if adm == "off":
            out.append(("info", "HA admission control is off: after a host failure there may be no room to restart its VMs",
                        "Cluster > Configure > vSphere Availability > Edit > Admission Control."))
        lvl, want = ha.get("failover_level_now"), int(ha.get("admission_hosts") or 0)
        if adm == "hosts" and lvl is not None and want and int(lvl) < want:
            out.append(("warning", "HA can survive %d host failure(s) now; admission control reserves %d" % (int(lvl), want),
                        "Add capacity or power off / move VMs (see the capacity report)."))
        if adm == "percent":
            short = []
            for label, left, keep in (("CPU", ha.get("cpu_left_pct"), ha.get("admission_cpu_pct")),
                                      ("memory", ha.get("mem_left_pct"), ha.get("admission_mem_pct"))):
                if left is not None and keep is not None and float(left) < float(keep):
                    short.append("%s %d%% left, %d%% reserved" % (label, int(left), int(keep)))
            if short:
                out.append(("warning", "HA failover capacity below what is reserved: " + "; ".join(short),
                            "Add capacity or power off / move VMs (see the capacity report)."))
    retreat = c.get("vcls_retreat")
    if retreat:
        fix = ("Retreat mode is set by hand: vCenter > Configure > Settings > Advanced Settings, config.vcls.clusters.%s.enabled. "
               "When the work it was set for is done, set it to true." % c.get("moid", "<cluster id>"))
        if drs.get("enabled"):
            out.append(("critical", "vSphere Cluster Services (vCLS) in retreat mode: DRS is not working and HA works less well", fix))
        else:
            out.append(("warning", "vSphere Cluster Services (vCLS) in retreat mode", fix))
    if not drs.get("enabled"):
        if o["require_drs"] and n >= 2:
            out.append(("warning", "DRS is off: VMs are not balanced, and maintenance mode does not move them",
                        "Cluster > Configure > vSphere DRS > Edit: turn it on (vm_health_require_drs: false where DRS is not licensed)."))
    else:
        if drs.get("behavior") == "manual":
            out.append(("info", "DRS is manual: its recommendations wait for someone to apply them",
                        "Cluster > Configure > vSphere DRS > Edit: fully automated, if nothing needs it manual."))
        if int(drs.get("recommendations") or 0):
            out.append(("warning", "%d DRS recommendation(s) waiting" % int(drs["recommendations"]),
                        "Cluster > Monitor > vSphere DRS > Recommendations: apply them."))
        if int(drs.get("faults") or 0):
            out.append(("warning", "DRS could not apply %d recommendation(s)" % int(drs["faults"]),
                        "Cluster > Monitor > vSphere DRS > Faults: each says why (affinity rules, a VM that cannot move ...)."))
    for i in c.get("config_issues") or []:
        out.append(("warning", i, "Cluster > Summary in vCenter (the issue is shown there)."))
    out += _alarm_problems(c.get("alarms"), "cluster", o["tz"])
    up = [h for h in hosts if _up(h)]
    cpu = _pct(sum(h.get("cpu_used_mhz") or 0 for h in up), sum(h.get("cpu_mhz") or 0 for h in up)) if up else None
    mem = _pct(sum(h.get("mem_used_mb") or 0 for h in up), sum(h.get("mem_mb") or 0 for h in up)) if up else None
    for label, p, w, cr in (("CPU", cpu, o["cpu_warn"], o["cpu_crit"]), ("Memory", mem, o["mem_warn"], o["mem_crit"])):
        sev = _usage_sev(p, w, cr)
        if sev:
            out.append((sev, "%s %s used across the cluster's hosts" % (label, _pct_text(p)),
                        "Add capacity or power off / move VMs (see the capacity report)."))
    out += _status_problem(c.get("status"), out, "cluster")
    return out


def _evaluate(data, opts):
    d, o = data or {}, _opts(opts)
    hosts = d.get("hosts") or []
    by_cl = {}
    for h in hosts:
        if h.get("cluster") and not h.get("standalone"):
            by_cl.setdefault((h.get("vcenter", ""), h.get("datacenter", ""), h["cluster"]), []).append(h)
    clusters = []
    for c in d.get("clusters") or []:
        hs = by_cl.get((c.get("vcenter", ""), c.get("datacenter", ""), c.get("name")), [])
        clusters.append((c, hs, cluster_problems(c, hs, o)))
    cl_by = {(c.get("vcenter", ""), c.get("datacenter", ""), c.get("name")): c for c, _, _ in clusters}
    hres = []
    for h in hosts:
        c = None if h.get("standalone") else cl_by.get((h.get("vcenter", ""), h.get("datacenter", ""), h.get("cluster")))
        hres.append((h, host_problems(h, c, o)))
    dres = []
    for dc in d.get("datacenters") or []:
        ps = [("warning", i, "Datacenter > Summary in vCenter.") for i in dc.get("config_issues") or []]
        ps += _alarm_problems(dc.get("alarms"), "datacenter", o["tz"])
        dres.append((dc, ps))
    vres = []
    for v in d.get("vcenter_info") or []:
        ps = _alarm_problems(v.get("alarms"), "vCenter", o["tz"])
        x = v.get("clock_offset_s")
        if x is not None and o["clock_warn"] and abs(x) >= o["clock_warn"]:
            ps.append(("warning", "vCenter's clock and the AAP job's differ: vCenter's is %s, so its event and alarm times are off "
                                  "by as much (or the AAP node's clock is)" % _secs(x),
                       "vCenter's appliance management (port 5480) > Time: NTP; and the AAP nodes' time sync (chronyc tracking)."))
        if v.get("vcls_readable") is False:
            ps.append(("info", "Could not read vCLS retreat mode (vCenter's advanced settings): the account may lack the privilege",
                       "Retreat mode still shows as a vCenter alarm on the cluster."))
        vres.append((v, ps))
    return o, clusters, hres, dres, vres


def _where(x, multi, *parts):
    bits = ([_short(x.get("vcenter"))] if multi and x.get("vcenter") else []) + [p for p in parts if p]
    return " / ".join(bits)


def vm_health_problems(data, opts=None):
    """Every problem found, most severe first: [{severity, object_type, object, vcenter, datacenter,
    cluster, problem, fix}]."""
    o, clusters, hres, dres, vres = _evaluate(data, opts)
    out = []
    for v, ps in vres:
        for sev, prob, fix in ps:
            out.append({"severity": sev, "object_type": "vcenter", "object": v.get("vcenter", ""), "vcenter": v.get("vcenter", ""),
                        "datacenter": "", "cluster": "", "problem": prob, "fix": fix})
    for dc, ps in dres:
        for sev, prob, fix in ps:
            out.append({"severity": sev, "object_type": "datacenter", "object": dc.get("name", ""), "vcenter": dc.get("vcenter", ""),
                        "datacenter": dc.get("name", ""), "cluster": "", "problem": prob, "fix": fix})
    for c, _, ps in clusters:
        for sev, prob, fix in ps:
            out.append({"severity": sev, "object_type": "cluster", "object": c.get("name", ""), "vcenter": c.get("vcenter", ""),
                        "datacenter": c.get("datacenter", ""), "cluster": c.get("name", ""), "problem": prob, "fix": fix})
    for h, ps in hres:
        for sev, prob, fix in ps:
            out.append({"severity": sev, "object_type": "host", "object": h.get("name", ""), "vcenter": h.get("vcenter", ""),
                        "datacenter": h.get("datacenter", ""), "cluster": h.get("cluster", ""), "problem": prob, "fix": fix})
    out.sort(key=lambda x: (RANK.get(x["severity"], 9), x["vcenter"], x["datacenter"], x["cluster"], x["object"]))
    return out


def _sev_count(ps):
    c = {}
    for p in ps:
        c[p[0]] = c.get(p[0], 0) + 1
    return ", ".join("%d %s" % (c[s], s) for s in ("critical", "warning", "info") if c.get(s)) or "-"


def _section(title, columns, rows, text="", max_rows=0):
    """rows: [(row status, [cells], [cell statuses])]."""
    more = len(rows) - max_rows if max_rows and len(rows) > max_rows else 0
    if more:
        rows = rows[:max_rows]
        text = (text + " " if text else "") + "Only the first %d are shown (%d more: see the job's artifacts or raise vm_health_max_rows)." % (max_rows, more)
    if not rows:
        return {"title": title, "text": text} if text else {"title": title, "lines": ["None."]}
    s = {"title": title, "columns": columns, "rows": [r[1] for r in rows],
         "row_status": [r[0] if r[0] in ("critical", "warning", "info", "unknown") else "" for r in rows],
         "cell_status": [[x if x in ("critical", "warning", "info", "ok", "unknown") else "" for x in r[2]] for r in rows]}
    if text:
        s["text"] = text
    st = _worst([r[0] for r in rows if r[0] in ("critical", "warning")])
    if st:
        s["status"] = st
    return s


def vm_health_report(data, opts=None):
    """The health report (roles/site_email layout)."""
    d = data or {}
    o, clusters, hres, dres, vres = _evaluate(d, opts)
    vcs = _vcs(d)
    multi = len(vcs) > 1
    problems = vm_health_problems(d, o)
    crit = [p for p in problems if p["severity"] == "critical"]
    warn = [p for p in problems if p["severity"] == "warning"]
    hosts = d.get("hosts") or []
    dcs = d.get("datacenters") or []
    notconn = [h for h in hosts if h.get("connection") not in ("connected", "")]
    ha_bad = [p for p in problems if p["severity"] in ("critical", "warning") and ("HA" in p["problem"] or "vCLS" in p["problem"])]
    drs_bad = [p for p in problems if p["severity"] in ("critical", "warning") and ("DRS" in p["problem"] or "vCLS" in p["problem"])]
    hw_bad = [h for h, ps in hres if any(p[1].startswith("Hardware ") for p in ps)]
    n_alarms = sum(len([a for a in (x.get("alarms") or []) if a.get("status") in ("red", "yellow")])
                   for x in list(d.get("clusters") or []) + hosts + dcs + list(d.get("vcenter_info") or []))
    counts = "%d datacenter(s), %d cluster(s), %d host(s)" % (len(dcs), len(clusters), len(hosts))
    if crit or warn:
        title = "VMware health report: %s - %s" % (", ".join(x for x in ("%d critical" % len(crit) if crit else "",
                                                                          "%d warning(s)" % len(warn) if warn else "") if x), counts)
    else:
        title = "VMware health report: healthy - " + counts
    status = "critical" if crit else ("warning" if warn else "ok")
    vc = ("vCenters " + ", ".join(vcs)) if multi else ("vCenter " + (d.get("vcenter") or ""))
    subtitle = "%s - every datacenter, cluster and host%s - read %s" % (
        vc, (" of datacenter " + o["datacenter"]) if o["datacenter"] else "", _when(d.get("read_at"), o["tz"]))
    if o["tz_note"]:
        subtitle += " - " + o["tz_note"]
    summary = [
        {"label": "Datacenters", "value": len(dcs), "status": status},
        {"label": "Clusters", "value": len(clusters), "status": _worst([p[0] for _, _, ps in clusters for p in ps if p[0] != "info"]) or "ok"},
        {"label": "Hosts", "value": len(hosts), "status": _worst([p[0] for _, ps in hres for p in ps if p[0] != "info"]) or "ok"},
        {"label": "Hosts not connected", "value": len(notconn), "status": "critical" if notconn else "ok"},
        {"label": "HA problems", "value": len(ha_bad), "status": _worst([p["severity"] for p in ha_bad]) or "ok"},
        {"label": "DRS problems", "value": len(drs_bad), "status": _worst([p["severity"] for p in drs_bad]) or "ok"},
        {"label": "Hosts with hardware faults", "value": len(hw_bad), "status": "critical" if any(
            p[0] == "critical" and p[1].startswith("Hardware ") for _, ps in hres for p in ps) else ("warning" if hw_bad else "ok")},
        {"label": "Triggered alarms", "value": n_alarms, "status": "warning" if n_alarms else "ok"},
    ]

    label = {"vcenter": "vCenter", "datacenter": "Datacenter", "cluster": "Cluster", "host": "Host"}
    rows = []
    for p in problems:
        where = _where(p, multi, p["datacenter"], p["cluster"] if p["object_type"] == "host" else "")
        rows.append((p["severity"], [p["severity"].upper(), "%s %s" % (label[p["object_type"]], p["object"]), p["problem"], p["fix"], where],
                     [p["severity"], "", "", "", ""]))
    sections = [_section("Needs attention", ["Severity", "Object", "Problem", "What to do", "Where"], rows,
                         "Red = critical (broken now), amber = warning (fix soon), blue = info (worth knowing, not broken)."
                         if rows else "Nothing needs attention: HA and DRS work on every cluster, every host is connected "
                                      "and its hardware is green, and no alarm is triggered.", o["max_rows"])]

    # datacenters
    rows = []
    for dc, ps in dres:
        key = (dc.get("vcenter", ""), dc.get("name", ""))
        cls = [x for x in clusters if (x[0].get("vcenter", ""), x[0].get("datacenter", "")) == key]
        hs = [x for x in hres if (x[0].get("vcenter", ""), x[0].get("datacenter", "")) == key]
        allp = ps + [p for _, _, cps in cls for p in cps] + [p for _, hps in hs for p in hps]
        st = _worst([p[0] for p in allp if p[0] != "info"]) or "ok"
        up = len([h for h, _ in hs if _up(h)])
        nc = len([h for h, _ in hs if h.get("connection") not in ("connected", "")])
        rows.append((st, [st.upper(), _where(dc, multi, dc.get("name", "")), len(cls), len(hs), up,
                          len([h for h, _ in hs if h.get("maintenance")]), nc,
                          len([a for a in dc.get("alarms") or [] if a.get("status") in ("red", "yellow")]), _sev_count(allp)],
                     [st, "", "", "", "", "", "critical" if nc else "", "", ""]))
    sections.append(_section("Datacenters", ["Status", "Datacenter", "Clusters", "Hosts", "Hosts running VMs", "In maintenance",
                                             "Not connected", "Alarms on it", "Problems (its clusters and hosts too)"], rows,
                             "Each datacenter with everything in it.", o["max_rows"]))

    # clusters
    rows = []
    for c, hs, ps in clusters:
        hps = [p for h in hs for p in host_problems(h, c, o)]
        st = _worst([p[0] for p in ps + hps if p[0] != "info"]) or "ok"
        ha, drs = c.get("ha") or {}, c.get("drs") or {}
        n = len(hs)
        up = len([h for h in hs if _up(h)])
        down = len([h for h in hs if h.get("connection") not in ("connected", "")])
        ha_hosts = [h for h in hs if _up(h) and h.get("ha_state") and HA_SEV.get(h["ha_state"], "warning") != "ok"]
        if not ha.get("enabled"):
            ha_txt, ha_st = "OFF", "warning" if o["require_ha"] and n >= 2 else ""
        else:
            adm = ha.get("admission")
            ha_txt = "on" + {"hosts": ", reserves %d host(s)" % int(ha.get("admission_hosts") or 0),
                             "percent": ", reserves CPU %s%% / memory %s%%" % (ha.get("admission_cpu_pct"), ha.get("admission_mem_pct")),
                             "dedicated hosts": ", %d failover host(s)" % int(ha.get("admission_hosts") or 0),
                             "off": ", admission control off"}.get(adm, "")
            if ha_hosts:
                ha_txt += "; %d host agent(s) not OK" % len(ha_hosts)
            ha_st = _worst([p[0] for p in ps if "HA" in p[1] and p[0] != "info"]
                           + [HA_SEV.get(h["ha_state"], "warning") for h in ha_hosts]) or "ok"
        if not drs.get("enabled"):
            drs_txt, drs_st = "OFF", "warning" if o["require_drs"] and n >= 2 else ""
        else:
            drs_txt = "on" + (" (%s)" % DRS_LABEL.get(drs.get("behavior"), drs.get("behavior")) if drs.get("behavior") else "")
            if drs.get("score") is not None:
                drs_txt += ", score %d%%" % int(drs["score"])
            if int(drs.get("recommendations") or 0):
                drs_txt += ", %d waiting" % int(drs["recommendations"])
            drs_st = _worst([p[0] for p in ps if ("DRS" in p[1] or "vCLS" in p[1]) and p[0] != "info"]) or "ok"
        retreat = c.get("vcls_retreat")
        vcls_txt, vcls_st = ("RETREAT MODE", "critical" if drs.get("enabled") else "warning") if retreat else (
            ("?", "") if retreat is None else ("normal", ""))
        live = [h for h in hs if _up(h)]
        cpu = _pct(sum(h.get("cpu_used_mhz") or 0 for h in live), sum(h.get("cpu_mhz") or 0 for h in live)) if live else None
        mem = _pct(sum(h.get("mem_used_mb") or 0 for h in live), sum(h.get("mem_mb") or 0 for h in live)) if live else None
        hosts_txt = "%d of %d" % (up, n) + (" (%d not connected)" % down if down else "")
        rows.append((st, [st.upper(), c.get("name", ""), _where(c, multi, c.get("datacenter", "")), hosts_txt, ha_txt, drs_txt, vcls_txt,
                          _pct_text(cpu), _pct_text(mem), len([a for a in c.get("alarms") or [] if a.get("status") in ("red", "yellow")]),
                          _sev_count(ps + hps)],
                     [st, "", "", "critical" if down else ("warning" if up < n else ""), ha_st, drs_st, vcls_st,
                      _usage_sev(cpu, o["cpu_warn"], o["cpu_crit"]), _usage_sev(mem, o["mem_warn"], o["mem_crit"]), "", ""]))
    sections.append(_section("Clusters", ["Status", "Cluster", "Datacenter", "Hosts running VMs", "vSphere HA", "DRS", "vCLS",
                                          "CPU used", "Memory used", "Alarms on it", "Problems (its hosts too)"], rows,
                             "HA restarts the VMs of a failed host; DRS balances the VMs and empties a host for maintenance; "
                             "vCLS keeps DRS running (retreat mode stops it). CPU and memory: of the hosts running VMs."
                             if rows else "No cluster.", o["max_rows"]))

    # hosts
    rows = []
    for h, ps in sorted(hres, key=lambda x: (x[0].get("vcenter", ""), x[0].get("datacenter", ""), x[0].get("cluster") or "~", x[0].get("name", ""))):
        st = _worst([p[0] for p in ps if p[0] != "info"]) or ("info" if any(p[0] == "info" for p in ps) else "ok")
        conn = h.get("connection", "")
        ha_state = h.get("ha_state") or ""
        c_ha = not h.get("standalone") and any(c.get("name") == h.get("cluster") and c.get("vcenter", "") == h.get("vcenter", "")
                                               and c.get("datacenter", "") == h.get("datacenter", "") and (c.get("ha") or {}).get("enabled")
                                               for c, _, _ in clusters)
        ha_txt = HA_LABEL.get(ha_state, ha_state) if c_ha and ha_state else ("-" if not c_ha else "?")
        ha_st = HA_SEV.get(ha_state, "") if c_ha and _up(h) and ha_state else ""
        bad = h.get("sensors_bad") or []
        hw_txt = ("%d red, %d yellow" % (len([s for s in bad if s.get("state") == "red"]), len([s for s in bad if s.get("state") == "yellow"]))
                  if bad else ("green" if conn == "connected" else "-"))
        hw_st = "critical" if any(s.get("state") == "red" for s in bad) else ("warning" if bad else "")
        cpu = _pct(h.get("cpu_used_mhz"), h.get("cpu_mhz")) if conn == "connected" else None
        mem = _pct(h.get("mem_used_mb"), h.get("mem_mb")) if conn == "connected" else None
        maint = "yes" if h.get("maintenance") else ("standby" if h.get("standby") in ("in", "entering") else "no")
        clock_st = _worst([p[0] for p in _clock_problems(h, o)])
        rows.append((st, [st.upper(), h.get("name", ""), "(standalone)" if h.get("standalone") else h.get("cluster", ""),
                          _where(h, multi, h.get("datacenter", "")), CONN_LABEL.get(conn, conn), maint, ha_txt, hw_txt, _pct_text(cpu), _pct_text(mem),
                          "-" if h.get("uptime_days") is None else h["uptime_days"],
                          _clock_cell(h.get("clock_offset_s")) if conn == "connected" else "-",
                          ("%s (%s)" % (h.get("version"), h.get("build"))) if h.get("version") else "",
                          len([a for a in h.get("alarms") or [] if a.get("status") in ("red", "yellow")])],
                     [st, "", "", "", "critical" if conn not in ("connected", "") else "", "info" if maint != "no" else "", ha_st, hw_st,
                      _usage_sev(cpu, o["cpu_warn"], o["cpu_crit"]), _usage_sev(mem, o["mem_warn"], o["mem_crit"]), "", clock_st, "", ""]))
    sections.append(_section("Hosts", ["Status", "Host", "Cluster", "Datacenter", "Connection", "Maintenance", "HA agent", "Hardware",
                                       "CPU used", "Memory used", "Up (days)", "Clock", "ESXi", "Alarms"], rows,
                             "Every host, by datacenter and cluster. Hardware: the sensors and hardware status that are not green. "
                             "Clock: the host's clock against vCenter's (+ ahead, - behind; ? = vCenter would not say) - the host "
                             "writes its own logs by it." if rows else "No host.", o["max_rows"]))

    rows = []
    for v, ps in vres:
        st = _worst([p[0] for p in ps if p[0] != "info"]) or "ok"
        rows.append((st, [st.upper(), v.get("vcenter", ""), v.get("name", "") or v.get("version", ""),
                          len([a for a in v.get("alarms") or [] if a.get("status") in ("red", "yellow")])], [st, "", "", ""]))
    sections.append(_section("vCenter", ["Status", "vCenter", "Version", "Alarms on vCenter itself"], rows, "", o["max_rows"]))
    return {"title": title, "status": status, "subtitle": subtitle, "summary": summary, "sections": sections,
            "footer": "Read-only. The alarms themselves, failed logins and the events of the day: the VMware alarms report."}


class FilterModule(object):
    def filters(self):
        return {"vm_health_report": vm_health_report, "vm_health_problems": vm_health_problems}
