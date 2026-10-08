# -*- coding: utf-8 -*-
"""Filters for vSphere capacity planning (roles/vmware_vm/tasks/capacity.yml): what
roles/vmware_vm/library/site_vmware_capacity.py read, turned into headroom (with one host failed),
growth trends fitted on vCenter's own daily statistics, runways (days until a limit), and a
report in the layout roles/site_email renders - overall first, then per cluster."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import math

RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4, "": 5}
DAY = 86400.0


def worst(statuses):
    s = sorted((x for x in statuses if x), key=lambda x: RANK.get(x, 9))
    return s[0] if s else ""


def size(n):
    """Bytes -> '1.5 TB' (binary units, as vSphere shows them)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    neg = n < 0
    n = abs(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            out = ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
            return ("-" if neg else "") + out
        n /= 1024.0


def ghz(mhz):
    try:
        return "%.1f GHz" % (float(mhz) / 1000.0)
    except (TypeError, ValueError):
        return "?"


def pct(a, b):
    try:
        return round(100.0 * float(a) / float(b), 1) if float(b) > 0 else None
    except (TypeError, ValueError):
        return None


def pct_text(p):
    return "?" if p is None else ("%.0f%%" % p)


def trend(samples, window_days=30, now=None, min_samples=7):
    """A straight line fitted (least squares) on the samples of the last window_days:
    samples [[seconds, value], ...] -> {slope (per day), r2 (how well a straight line fits, 0-1),
    n, span (days)} or None when there are too few (fewer than min_samples, or under 3 days)."""
    pts = [(float(t), float(v)) for t, v in samples or [] if v is not None]
    if not pts:
        return None
    end = float(now) if now else max(t for t, _ in pts)
    pts = [(t, v) for t, v in pts if t >= end - window_days * DAY]
    if len(pts) < max(2, int(min_samples)):
        return None
    span = (max(t for t, _ in pts) - min(t for t, _ in pts)) / DAY
    if span < 3:
        return None
    n = float(len(pts))
    mx = sum(t for t, _ in pts) / n
    my = sum(v for _, v in pts) / n
    sxx = sum((t - mx) ** 2 for t, _ in pts)
    if sxx == 0:
        return None
    sxy = sum((t - mx) * (v - my) for t, v in pts)
    slope = sxy / sxx                       # per second
    syy = sum((v - my) ** 2 for _, v in pts)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 1.0
    return {"slope": slope * DAY, "r2": round(r2, 2), "n": int(n), "span": round(span, 1)}


def days_until(current, slope, target):
    """Days until current reaches target at slope per day: 0 when it is there already, None when
    it is not growing (or nothing is known)."""
    if current is None or target is None or target <= 0:
        return None
    if current >= target:
        return 0
    if slope is None or slope <= 0:
        return None
    return int(math.floor((target - current) / slope))


def runway_text(days, now=None):
    if days is None:
        return "not growing"
    if days == 0:
        return "reached"
    if days >= 3650:                        # before the date: a tiny growth gives a date past year 9999
        return "10+ years"
    when = (now or datetime.datetime.now(datetime.timezone.utc)) + datetime.timedelta(days=days)
    return "%d days (%s)" % (days, when.strftime("%Y-%m-%d"))


def days_text(days):
    """12 -> '12 days'; None -> 'not growing'; past ten years -> '10+ years'."""
    if days is None:
        return "not growing"
    return "10+ years" if days >= 3650 else "%d days" % days


def fit_text(t):
    if not t:
        return "not enough history"
    q = "good" if t["r2"] >= 0.8 else ("fair" if t["r2"] >= 0.5 else "poor")
    return "%s (R2 %.2f, %d days)" % (q, t["r2"], int(round(t["span"])))


def runway_status(days, warn, crit):
    if days is None:
        return "ok"
    return "critical" if days <= crit else ("warning" if days <= warn else "ok")


def level(p, warn, crit):
    if p is None:
        return ""
    return "critical" if p >= crit else ("warning" if p >= warn else "ok")


# ---- the numbers ---------------------------------------------------------------------------------
def hosts_for(used, cap, per_host, target):
    """How many more hosts of per_host capacity bring used to at most target (0-1) of cap - None when
    unknown. 0 = none needed."""
    if not per_host or per_host <= 0 or not target or used is None:
        return None
    need = used / float(target) - cap
    return 0 if need <= 0 else int(math.ceil(need / per_host - 1e-9))


def cluster_numbers(c, o, now_ts):
    """One cluster's capacity, usage, headroom with failover_hosts host(s) failed, VMs that still fit,
    trends and runways."""
    usable = [h for h in c.get("hosts") or [] if h.get("connection") == "connected" and not h.get("maintenance")]
    fail = 0 if c.get("standalone") else max(int(o.get("failover_hosts", 1)), int((c.get("ha") or {}).get("hosts") or 0))
    cores = sum(h.get("cores", 0) for h in usable)
    cpu = sum(h.get("cpu_mhz", 0) for h in usable)
    mem = sum(h.get("mem_bytes", 0) for h in usable)
    cpu_used = sum(h.get("cpu_used_mhz", 0) for h in usable)
    mem_used = sum(h.get("mem_used_bytes", 0) for h in usable)
    big_mem = sorted((h.get("mem_bytes", 0) for h in usable), reverse=True)[:fail]
    big_cpu = sorted((h.get("cpu_mhz", 0) for h in usable), reverse=True)[:fail]
    big_cores = sorted((h.get("cores", 0) for h in usable), reverse=True)[:fail]
    survives = len(usable) > fail
    mem_n1 = mem - sum(big_mem) if survives else 0
    cpu_n1 = cpu - sum(big_cpu) if survives else 0
    cores_n1 = cores - sum(big_cores) if survives else 0
    mt, ct = float(o.get("mem_target_pct", 80)) / 100.0, float(o.get("cpu_target_pct", 80)) / 100.0
    ratio_max = float(o.get("vcpu_per_core_max", 4))
    vms = int(c.get("vms_on") or 0)
    avg = {"vcpu": c.get("vcpus_on", 0) / float(vms) if vms else None,
           "mem_configured": c.get("mem_configured_on", 0) / float(vms) if vms else None,
           "mem_used": mem_used / float(vms) if vms else None, "cpu_used": cpu_used / float(vms) if vms else None}
    room = {"memory": mem_n1 * mt - mem_used, "cpu": cpu_n1 * ct - cpu_used, "vcpu": cores_n1 * ratio_max - c.get("vcpus_on", 0)}
    fits = {}
    if vms:
        if avg["mem_used"]:
            fits["memory"] = max(0, int(room["memory"] // avg["mem_used"]))
        if avg["cpu_used"]:
            fits["CPU"] = max(0, int(room["cpu"] // avg["cpu_used"]))
        if avg["vcpu"]:
            fits["vCPUs per core"] = max(0, int(room["vcpu"] // avg["vcpu"]))
    limit = min(fits, key=lambda k: fits[k]) if fits else ""
    wd, ms = float(o.get("trend_days", 30)), int(o.get("min_samples", 7))
    tm = trend((c.get("history") or {}).get("mem"), wd, now_ts, ms)
    tc = trend((c.get("history") or {}).get("cpu"), wd, now_ts, ms)
    mem_days = days_until(mem_used, tm["slope"] if tm else None, mem_n1 * mt) if survives else 0
    cpu_days = days_until(cpu_used, tc["slope"] if tc else None, cpu_n1 * ct) if survives else 0
    ev_days = float(o.get("events_days", 30)) or 30
    # hosts recommended (of this cluster's average host size): memory and CPU with failover_hosts
    # failed at most at their target - now, and at the current growth after horizon_months
    per_mem = mem / float(len(usable)) if usable else 0
    per_cpu = cpu / float(len(usable)) if usable else 0
    months = float(o.get("horizon_months", 12))
    grow = lambda used, t: used + max(0.0, t["slope"]) * months * 30.4 if t else used
    now_n = [x for x in (hosts_for(mem_used, mem_n1, per_mem, mt), hosts_for(cpu_used, cpu_n1, per_cpu, ct)) if x is not None]
    later_n = [x for x in (hosts_for(grow(mem_used, tm), mem_n1, per_mem, mt), hosts_for(grow(cpu_used, tc), cpu_n1, per_cpu, ct))
               if x is not None]
    hosts_now = max(now_n) if now_n else None
    hosts_later = max(later_n) if later_n else None
    avg_after = pct(mem_used, mem + (hosts_now or 0) * per_mem) if hosts_now else None
    return {"name": c.get("name"), "datacenter": c.get("datacenter", ""), "standalone": bool(c.get("standalone")),
            "hosts_total": len(c.get("hosts") or []), "hosts_usable": len(usable), "failover_hosts": fail, "survives": survives,
            "ha": c.get("ha") or {}, "cores": cores, "cpu": cpu, "mem": mem, "cpu_used": cpu_used, "mem_used": mem_used,
            "cpu_n1": cpu_n1, "mem_n1": mem_n1, "cores_n1": cores_n1,
            "cpu_pct": pct(cpu_used, cpu), "mem_pct": pct(mem_used, mem), "cpu_n1_pct": pct(cpu_used, cpu_n1), "mem_n1_pct": pct(mem_used, mem_n1),
            "vms_on": vms, "vms_off": c.get("vms_off", 0), "vcpus_on": c.get("vcpus_on", 0),
            "vcpu_ratio": round(c.get("vcpus_on", 0) / float(cores_n1 or cores), 2) if (cores_n1 or cores) else None,
            "avg": avg, "fits": fits, "fit": fits.get(limit) if limit else None, "limit": limit,
            "mem_trend": tm, "cpu_trend": tc, "mem_days": mem_days, "cpu_days": cpu_days,
            "added": c.get("added", 0), "removed": c.get("removed", 0),
            "hosts_now": hosts_now, "hosts_later": hosts_later, "horizon_months": int(months), "mem_avg_after": avg_after,
            "host_mem": per_mem, "host_cores": cores / float(len(usable)) if usable else 0,
            "net_per_month": round((c.get("added", 0) - c.get("removed", 0)) * 30.0 / ev_days, 1),
            "hosts": c.get("hosts") or []}


def cluster_status(n, o):
    mt, ct = float(o.get("mem_target_pct", 80)), float(o.get("cpu_target_pct", 80))
    warn_d, crit_d = float(o.get("runway_warn_days", 90)), float(o.get("runway_crit_days", 30))
    st = {"mem": "", "cpu": "", "n1": "", "vcpu": "", "mem_days": "", "cpu_days": "", "hosts": ""}
    st["mem"] = level(n["mem_pct"], mt, 100)
    st["cpu"] = level(n["cpu_pct"], ct, 100)
    if n["failover_hosts"]:
        st["n1"] = "critical" if not n["survives"] or (n["mem_n1_pct"] or 0) >= 100 else level(n["mem_n1_pct"], mt, 100)
    st["vcpu"] = level(n["vcpu_ratio"], float(o.get("vcpu_per_core_max", 4)), float(o.get("vcpu_per_core_max", 4)) * 1.5)
    st["mem_days"] = runway_status(n["mem_days"], warn_d, crit_d)
    st["cpu_days"] = runway_status(n["cpu_days"], warn_d, crit_d)
    st["hosts"] = "warning" if n["hosts_usable"] < n["hosts_total"] else "ok"
    return st, worst(st.values())


def datastore_numbers(d, o, now_ts):
    cap, free = d.get("capacity") or 0, d.get("free") or 0
    used = cap - free
    t = trend(d.get("history"), float(o.get("trend_days", 30)), now_ts, int(o.get("min_samples", 7)))
    sw, sc = float(o.get("storage_warn_pct", 85)), float(o.get("storage_crit_pct", 90))
    slope = t["slope"] if t else None
    return {"name": d.get("name"), "datacenter": d.get("datacenter", ""), "clusters": d.get("clusters") or [], "pod": d.get("pod", ""),
            "type": d.get("type", ""), "accessible": d.get("accessible", True),
            "shared": bool(d.get("shared")) or int(d.get("hosts") or 0) >= 2,    # mounted by two hosts or more
            "capacity": cap, "free": free, "used": used, "pct": pct(used, cap),
            "prov_pct": pct(used + (d.get("uncommitted") or 0), cap), "trend": t, "slope": slope,
            "days_warn": days_until(used, slope, cap * sw / 100.0), "days_crit": days_until(used, slope, cap * sc / 100.0),
            "days_full": days_until(used, slope, cap)}


def datastore_status(d, o):
    sw, sc = float(o.get("storage_warn_pct", 85)), float(o.get("storage_crit_pct", 90))
    warn_d, crit_d = float(o.get("runway_warn_days", 90)), float(o.get("runway_crit_days", 30))
    if not d["accessible"]:
        return "critical"
    return worst([level(d["pct"], sw, sc), runway_status(d["days_crit"], warn_d, crit_d)])


def capacity_numbers(data, opts=None):
    """Everything the report and ACT use: {'clusters': [...], 'datastores': [...], 'overall': {...}}."""
    d, o = data or {}, opts or {}
    now = datetime.datetime.strptime(d.get("read_at", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc) \
        if d.get("read_at") else datetime.datetime.now(datetime.timezone.utc)
    now_ts = now.timestamp()
    local = bool(o.get("include_local_datastores"))
    cls = []
    for c in d.get("clusters") or []:
        n = cluster_numbers(c, o, now_ts)
        n["status_cells"], n["status"] = cluster_status(n, o)
        cls.append(n)
    dss = []
    for x in d.get("datastores") or []:
        n = datastore_numbers(x, o, now_ts)
        n["status"] = datastore_status(n, o)
        dss.append(n)
    counted = [x for x in dss if x["shared"] or local]
    for n in cls:
        mine = [x for x in counted if n["name"] in x["clusters"]]
        cap = sum(x["capacity"] for x in mine)
        used = sum(x["used"] for x in mine)
        slope = sum(x["slope"] for x in mine if x["slope"]) if any(x["slope"] for x in mine) else None
        sw, sc = float(o.get("storage_warn_pct", 85)), float(o.get("storage_crit_pct", 90))
        n["storage"] = {"datastores": len(mine), "capacity": cap, "used": used, "free": cap - used, "pct": pct(used, cap),
                        "prov_pct": pct(sum(x["used"] for x in mine) + sum(dd.get("uncommitted") or 0 for dd in d.get("datastores") or []
                                                                           if dd.get("name") in [x["name"] for x in mine]), cap),
                        "slope": slope, "days_warn": days_until(used, slope, cap * sw / 100.0),
                        "days_crit": days_until(used, slope, cap * sc / 100.0), "days_full": days_until(used, slope, cap),
                        "status": worst([x["status"] for x in mine])}
    mt, ct = float(o.get("mem_target_pct", 80)) / 100.0, float(o.get("cpu_target_pct", 80)) / 100.0
    tot = lambda k: sum(n[k] or 0 for n in cls)
    mem_slope = sum(n["mem_trend"]["slope"] for n in cls if n["mem_trend"]) if any(n["mem_trend"] for n in cls) else None
    cpu_slope = sum(n["cpu_trend"]["slope"] for n in cls if n["cpu_trend"]) if any(n["cpu_trend"] for n in cls) else None
    s_cap = sum(x["capacity"] for x in counted)
    s_used = sum(x["used"] for x in counted)
    s_unc = sum(dd.get("uncommitted") or 0 for dd in d.get("datastores") or [] if dd.get("shared") or local)
    s_slope = sum(x["slope"] for x in counted if x["slope"]) if any(x["slope"] for x in counted) else None
    sw, sc = float(o.get("storage_warn_pct", 85)), float(o.get("storage_crit_pct", 90))
    vms, vcpus = tot("vms_on"), tot("vcpus_on")
    overall = {"clusters": len(cls), "hosts": tot("hosts_total"), "hosts_usable": tot("hosts_usable"), "cores": tot("cores"),
               "cpu": tot("cpu"), "cpu_used": tot("cpu_used"), "cpu_n1": tot("cpu_n1"), "mem": tot("mem"), "mem_used": tot("mem_used"),
               "mem_n1": tot("mem_n1"), "vms_on": vms, "vcpus_on": vcpus, "added": tot("added"), "removed": tot("removed"),
               "net_per_month": round(sum(n["net_per_month"] for n in cls), 1), "fit": sum(n["fit"] or 0 for n in cls),
               "mem_slope": mem_slope, "cpu_slope": cpu_slope,
               "storage": {"capacity": s_cap, "used": s_used, "free": s_cap - s_used, "pct": pct(s_used, s_cap),
                           "prov_pct": pct(s_used + s_unc, s_cap), "slope": s_slope,
                           "days_warn": days_until(s_used, s_slope, s_cap * sw / 100.0),
                           "days_crit": days_until(s_used, s_slope, s_cap * sc / 100.0), "days_full": days_until(s_used, s_slope, s_cap)}}
    overall["cpu_pct"], overall["mem_pct"] = pct(overall["cpu_used"], overall["cpu"]), pct(overall["mem_used"], overall["mem"])
    overall["cpu_n1_pct"], overall["mem_n1_pct"] = pct(overall["cpu_used"], overall["cpu_n1"]), pct(overall["mem_used"], overall["mem_n1"])
    overall["vcpu_ratio"] = round(vcpus / float(overall["cores"]), 2) if overall["cores"] else None
    overall["hosts_now"] = sum(n["hosts_now"] or 0 for n in cls)
    overall["hosts_later"] = sum(max(n["hosts_later"] or 0, n["hosts_now"] or 0) for n in cls)
    overall["horizon_months"] = int(float(o.get("horizon_months", 12)))
    overall["mem_days"] = days_until(overall["mem_used"], mem_slope, overall["mem_n1"] * mt)
    overall["cpu_days"] = days_until(overall["cpu_used"], cpu_slope, overall["cpu_n1"] * ct)
    return {"now": now, "clusters": cls, "datastores": dss, "overall": overall}


# ---- the report ----------------------------------------------------------------------------------
def hosts_text(n):
    """'1 now (memory 66% on average then); 2 within 12 months' - how many hosts of the cluster's
    average size are recommended to keep memory and CPU at their targets with a host failed."""
    if n.get("hosts_now") is None and n.get("hosts_later") is None:
        return "?"
    now_, later = n.get("hosts_now") or 0, n.get("hosts_later") or 0
    m = int(n.get("horizon_months") or 12)
    if not now_ and not later:
        return "none within %d months" % m
    first = ("%d now (memory %s on average then)" % (now_, pct_text(n.get("mem_avg_after")))) if now_ else "none now"
    return first + ("; %d within %d months" % (later, m) if later > now_ else "")


def _per_month(slope, fmt=size):
    if slope is None:
        return "?"
    return ("+" if slope >= 0 else "") + fmt(slope * 30) + " / month"


def _row(cells, sts=None, st=None):
    sts = list(sts or [])
    sts += [""] * (len(cells) - len(sts))
    return (["" if x is None else x for x in cells], sts, st if st is not None else worst(sts))


def _section(title, text, columns, rows, none_text, max_rows=0, keep_order=False, lines=None):
    if not keep_order:
        rows = sorted(rows, key=lambda r: RANK.get(r[2], 9))
    more = len(rows) - max_rows if max_rows and len(rows) > max_rows else 0
    rows = rows[:max_rows] if more else rows
    s = {"title": title}
    if rows:
        s.update({"columns": columns, "rows": [r[0] for r in rows], "cell_status": [r[1] for r in rows],
                  "row_status": [r[2] if r[2] in ("critical", "warning", "info", "unknown") else "" for r in rows],
                  "text": text + (" The first %d of %d are shown; the artifacts (vm_capacity) have all." % (max_rows, len(rows) + more) if more else "")})
    else:
        s["text"] = (text + " " + none_text).strip()
    st = worst([r[2] for r in rows if r[2] in ("critical", "warning")])
    if st:
        s["status"] = st
    if lines:
        s["lines"] = lines
    return s


def vm_capacity_report(data, opts=None):
    """The capacity planning report -> {'report': ..., 'numbers': capacity_numbers(...), 'items': [...]} (items: what
    ACT is asked about, problems first)."""
    o = opts or {}
    N = capacity_numbers(data, o)
    now, cls, dss, ov = N["now"], N["clusters"], N["datastores"], N["overall"]
    d = data or {}
    mt, ct = float(o.get("mem_target_pct", 80)), float(o.get("cpu_target_pct", 80))
    rmax = float(o.get("vcpu_per_core_max", 4))
    warn_d, crit_d = float(o.get("runway_warn_days", 90)), float(o.get("runway_crit_days", 30))
    sw, sc = float(o.get("storage_warn_pct", 85)), float(o.get("storage_crit_pct", 90))
    max_rows = int(o.get("max_rows") or 100)
    fail = int(o.get("failover_hosts", 1))
    sections = []

    # ---- overall ----
    s = ov["storage"]
    rows = [
        _row(["CPU", ghz(ov["cpu"]), ghz(ov["cpu_used"]), pct_text(ov["cpu_pct"]), pct_text(ov["cpu_n1_pct"]),
              _per_month(ov["cpu_slope"], ghz), runway_text(ov["cpu_days"], now) + " to %s%%" % int(ct)],
             ["", "", "", level(ov["cpu_pct"], ct, 100), level(ov["cpu_n1_pct"], ct, 100), "", runway_status(ov["cpu_days"], warn_d, crit_d)]),
        _row(["Memory", size(ov["mem"]), size(ov["mem_used"]), pct_text(ov["mem_pct"]), pct_text(ov["mem_n1_pct"]),
              _per_month(ov["mem_slope"]), runway_text(ov["mem_days"], now) + " to %s%%" % int(mt)],
             ["", "", "", level(ov["mem_pct"], mt, 100), level(ov["mem_n1_pct"], mt, 100), "", runway_status(ov["mem_days"], warn_d, crit_d)]),
        _row(["Hosts", "%d usable of %d" % (ov["hosts_usable"], ov["hosts"]), "", "", "", "",
              ("%d recommended now, %d within %d months" % (ov["hosts_now"], ov["hosts_later"], ov["horizon_months"]))
              if ov["hosts_later"] else "none recommended within %d months" % ov["horizon_months"]],
             ["", "", "", "", "", "", "critical" if ov["hosts_now"] else ("warning" if ov["hosts_later"] else "ok")]),
        _row(["vCPUs", "%d cores" % ov["cores"], "%d vCPUs" % ov["vcpus_on"], "%s per core" % (ov["vcpu_ratio"] if ov["vcpu_ratio"] is not None else "?"),
              "", "%+.1f VMs / month" % ov["net_per_month"], "room for about %d more average VMs" % ov["fit"]],
             ["", "", "", level(ov["vcpu_ratio"], rmax, rmax * 1.5)]),
        _row(["Storage (shared datastores)", size(s["capacity"]), size(s["used"]), pct_text(s["pct"]), "",
              _per_month(s["slope"]), runway_text(s["days_crit"], now) + " to %s%%; full: %s" % (int(sc), runway_text(s["days_full"], now))],
             ["", "", "", level(s["pct"], sw, sc), "", "", runway_status(s["days_crit"], warn_d, crit_d)]) if s["capacity"] else
        _row(["Storage (shared datastores)", "-", "-", "-", "", "", "no shared datastore"]),
    ]
    sections.append(_section("Overall", "All %d cluster(s) of vCenter %s together: %d hosts (%d usable), %d VMs powered on. "
                             "\"After host failure\" is the use with %d host(s) of each cluster down (N+%d). Growth is fitted on vCenter's "
                             "daily statistics of the last %s days." % (ov["clusters"], d.get("vcenter", ""), ov["hosts"], ov["hosts_usable"],
                                                                        ov["vms_on"], fail, fail, int(float(o.get("trend_days", 30)))),
                             ["Resource", "Capacity", "Used", "Used %", "Used after host failure", "Growth", "Runway"], rows, "", keep_order=True))

    # ---- per cluster: compute ----
    rows = []
    for n in cls:
        c = n["status_cells"]
        avg = n["avg"]
        avg_txt = ("%.1f vCPU, %s" % (avg["vcpu"], size(avg["mem_configured"]))) if avg["vcpu"] else "-"
        rows.append(_row([n["name"], n["datacenter"], "%d of %d" % (n["hosts_usable"], n["hosts_total"]), pct_text(n["cpu_pct"]),
                          pct_text(n["mem_pct"]),
                          (pct_text(n["mem_n1_pct"]) if n["survives"] else "cannot lose a host") if n["failover_hosts"] else "standalone",
                          n["vcpu_ratio"] if n["vcpu_ratio"] is not None else "?", n["vms_on"], avg_txt,
                          _per_month(n["mem_trend"]["slope"]) if n["mem_trend"] else "not enough history",
                          runway_text(n["mem_days"], now), ("%d (%s)" % (n["fit"], n["limit"])) if n["fit"] is not None else "?",
                          hosts_text(n), "%+d / %d" % (n["added"], -n["removed"]) if (n["added"] or n["removed"]) else "0"],
                         ["", "", c["hosts"], c["cpu"], c["mem"], c["n1"], c["vcpu"], "", "", "", c["mem_days"],
                          "critical" if n["fit"] == 0 and n["vms_on"] else ("warning" if (n["fit"] or 0) < 5 and n["vms_on"] else "ok"),
                          "critical" if n["hosts_now"] else ("warning" if n["hosts_later"] else "ok")], n["status"]))
    sections.append(_section("Clusters: CPU and memory", "Per cluster: hosts usable (connected, not in maintenance), CPU and memory used now, "
                             "memory used if the biggest %d host(s) fail (the cluster must still run everything: amber from %s%%, red at "
                             "100%%), vCPUs per physical core (amber from %s), VMs powered on and their average size, memory growth, the "
                             "days until memory after a host failure reaches %s%%, how many more VMs of the average size fit (and what "
                             "limits them), how many hosts of the cluster's average size are recommended to keep memory and CPU at %s%% / %s%% "
                             "with a host down (now, and at the current growth within %d months), and VMs added / removed in the last %s days."
                             % (fail, int(mt), rmax, int(mt), int(mt), int(ct), int(float(o.get("horizon_months", 12))), int(float(o.get("events_days", 30)))),
                             ["Cluster", "Datacenter", "Hosts", "CPU used", "Memory used", "Memory after host failure", "vCPU per core",
                              "VMs on", "Average VM", "Memory growth", "Memory runway", "Room for more VMs", "Hosts recommended",
                              "VMs +/- (%s d)" % int(float(o.get("events_days", 30)))],
                             rows, "No cluster.", max_rows))

    # ---- per cluster: storage ----
    rows = []
    for n in cls:
        st = n["storage"]
        if not st["datastores"]:
            continue
        rows.append(_row([n["name"], st["datastores"], size(st["capacity"]), size(st["free"]), pct_text(st["pct"]), pct_text(st["prov_pct"]),
                          _per_month(st["slope"]), runway_text(st["days_warn"], now), runway_text(st["days_full"], now)],
                         ["", "", "", "", level(st["pct"], sw, sc), "warning" if (st["prov_pct"] or 0) > 150 else "", "",
                          runway_status(st["days_warn"], warn_d, crit_d), runway_status(st["days_full"], warn_d, crit_d)]))
    sections.append(_section("Clusters: storage", "Per cluster, its shared datastores together: capacity, free, used %%, provisioned %% "
                             "(thin disks counted at their full size: over 100%% means overcommitted), growth, and the days until %s%% and "
                             "until full at that growth." % int(sw),
                             ["Cluster", "Datastores", "Capacity", "Free", "Used", "Provisioned", "Growth", "Runway to %s%%" % int(sw), "Runway to full"],
                             rows, "No shared datastore.", max_rows))

    # ---- datastores ----
    rows = []
    for x in sorted(dss, key=lambda x: (x["days_crit"] if x["days_crit"] is not None else 99999, -(x["pct"] or 0))):
        if not (x["shared"] or o.get("include_local_datastores")):
            continue
        rows.append(_row([x["name"], ", ".join(x["clusters"]), x["pod"], x["type"], size(x["capacity"]), size(x["free"]), pct_text(x["pct"]),
                          pct_text(x["prov_pct"]), _per_month(x["slope"]), runway_text(x["days_warn"], now), runway_text(x["days_crit"], now),
                          runway_text(x["days_full"], now), fit_text(x["trend"])],
                         ["", "", "", "", "", "", level(x["pct"], sw, sc), "warning" if (x["prov_pct"] or 0) > 150 else "", "",
                          runway_status(x["days_warn"], warn_d, crit_d), runway_status(x["days_crit"], warn_d, crit_d),
                          runway_status(x["days_full"], warn_d, crit_d), "warning" if x["trend"] and x["trend"]["r2"] < 0.5 else ""], x["status"]))
    sections.append(_section("Datastores: runway", "Each shared datastore, the ones that reach %s%% first on top: growth fitted on vCenter's "
                             "daily used space, the days (and date) until %s%%, %s%% and full, and how well a straight line fits the history "
                             "(poor = jumps: snapshots deleted, VMs moved - the runway is a rough guide then)." % (int(sc), int(sw), int(sc)),
                             ["Datastore", "Clusters", "Datastore cluster", "Type", "Capacity", "Free", "Used", "Provisioned", "Growth",
                              "Runway to %s%%" % int(sw), "Runway to %s%%" % int(sc), "Runway to full", "Trend fit"],
                             rows, "No shared datastore.", max_rows, keep_order=True))

    # ---- hosts ----
    rows = []
    for n in cls:
        for h in n["hosts"]:
            state = "MAINTENANCE" if h.get("maintenance") else str(h.get("connection", "")).upper()
            hst = "info" if h.get("maintenance") else ("ok" if h.get("connection") == "connected" else "critical")
            mp = pct(h.get("mem_used_bytes"), h.get("mem_bytes"))
            cp = pct(h.get("cpu_used_mhz"), h.get("cpu_mhz"))
            rows.append(_row([n["name"], h.get("name"), state, h.get("cores"), pct_text(cp), size(h.get("mem_bytes")), pct_text(mp), h.get("vms_on", 0)],
                             ["", "", hst, "", level(cp, 80, 90), "", level(mp, 85, 95)], hst if hst != "ok" else worst([level(cp, 80, 90), level(mp, 85, 95)])))
    sections.append(_section("Hosts per cluster", "Every host: state (blue: in maintenance, red: not connected - neither counts as capacity), "
                             "cores, CPU and memory used now, VMs. Hosts much fuller than their neighbours mean DRS is off or limited.",
                             ["Cluster", "Host", "State", "Cores", "CPU used", "Memory", "Memory used", "VMs on"],
                             sorted(rows, key=lambda r: (r[0][0], r[0][1])), "No host.", max_rows * 3, keep_order=True))

    # ---- items for ACT, title, summary ----
    shared = [x for x in dss if x["shared"] or o.get("include_local_datastores")]

    def first(pairs):
        """(days, what) of the limit reached first, from [(days or None, what)]."""
        known = [(dd, w) for dd, w in pairs if dd is not None]
        return min(known) if known else (None, "")
    od, ow = first([(ov["mem_days"], "memory"), (ov["cpu_days"], "CPU"), (ov["storage"]["days_crit"], "storage")])
    items = [{"id": "P1", "kind": "overall", "name": "all clusters", "job_days": od, "job_limit": ow,
              "status": worst([n["status"] for n in cls] + [x["status"] for x in shared]) or "ok"}]
    for n in sorted(cls, key=lambda n: RANK.get(n["status"], 9)):
        dd, w = first([(n["mem_days"], "memory"), (n["cpu_days"], "CPU"), (n["storage"]["days_crit"], "storage")])
        items.append({"kind": "cluster", "name": n["name"], "status": n["status"] or "ok", "job_days": dd, "job_limit": w})
    for x in sorted(shared, key=lambda x: RANK.get(x["status"], 9)):
        items.append({"kind": "datastore", "name": x["name"], "status": x["status"] or "ok", "job_days": x["days_crit"],
                      "job_limit": "%s%% full" % int(sc) if x["days_crit"] is not None else ""})
    items = items[:1] + sorted(items[1:], key=lambda i: RANK.get(i["status"], 9))
    for k, it in enumerate(items, 1):
        it["id"] = "P%d" % k
    bad_c = [n for n in cls if n["status"] in ("critical", "warning")]
    bad_d = [x for x in dss if x["status"] in ("critical", "warning") and (x["shared"] or o.get("include_local_datastores"))]
    parts = []
    if bad_c:
        parts.append("%d of %d cluster(s) short on CPU or memory" % (len(bad_c), len(cls)))
    if bad_d:
        parts.append("%d datastore(s) at %s%% or within %d days of %s%%" % (len(bad_d), int(sw), int(warn_d), int(sc)))
    title = "vSphere capacity planning: " + ("; ".join(parts) if parts else
                                             "all %d cluster(s) and their datastores have room for %d+ days" % (len(cls), int(warn_d)))
    status = "critical" if any(n["status"] == "critical" for n in cls) or any(x["status"] == "critical" for x in bad_d) else (
        "warning" if parts else "ok")
    summary = [{"label": "Clusters", "value": len(cls)},
               {"label": "Hosts usable", "value": "%d of %d" % (ov["hosts_usable"], ov["hosts"]), "status": "ok" if ov["hosts_usable"] == ov["hosts"] else "warning"},
               {"label": "VMs powered on", "value": ov["vms_on"]},
               {"label": "Memory used", "value": pct_text(ov["mem_pct"]), "status": level(ov["mem_pct"], mt, 100) or "ok"},
               {"label": "Memory after host failure", "value": pct_text(ov["mem_n1_pct"]), "status": level(ov["mem_n1_pct"], mt, 100) or "ok"},
               {"label": "vCPU per core", "value": ov["vcpu_ratio"] if ov["vcpu_ratio"] is not None else "?",
                "status": level(ov["vcpu_ratio"], rmax, rmax * 1.5) or "ok"},
               {"label": "Room for more VMs", "value": "~%d" % ov["fit"]},
               {"label": "Storage used", "value": pct_text(s["pct"]), "status": level(s["pct"], sw, sc) or "ok"},
               {"label": "Storage runway to %s%%" % int(sc), "value": days_text(s["days_crit"]) if s["capacity"] else "-",
                "status": runway_status(s["days_crit"], warn_d, crit_d)}]
    notes = list(d.get("notes") or [])
    if not any(n["mem_trend"] for n in cls) and not any(x["trend"] for x in dss):
        notes.append("No trend: vCenter has fewer than %s daily samples (a new vCenter, or its statistics level or interval was changed). "
                     "Usage now is still shown." % o.get("min_samples", 7))
    if notes:
        sections.append({"title": "Notes", "lines": notes})
    report = {"title": title, "status": status, "summary": summary, "sections": sections,
              "subtitle": "vCenter %s - read %s UTC%s%s" % (d.get("vcenter", ""), now.strftime("%Y-%m-%d %H:%M"),
                                                           " - datacenter " + o["datacenter"] if o.get("datacenter") else "",
                                                           " - clusters " + ", ".join(o["clusters"]) if o.get("clusters") else ""),
              "footer": "Estimates from a straight line over the last %s days of vCenter's daily statistics: a guide for planning, not a "
                        "promise. Read-only." % int(float(o.get("trend_days", 30)))}
    return {"report": report, "numbers": _plain(N), "items": items}


def _plain(N):
    """The numbers without datetimes (for artifacts)."""
    out = dict(N)
    out["now"] = N["now"].strftime("%Y-%m-%dT%H:%M:%SZ")
    out["clusters"] = [{k: v for k, v in c.items() if k not in ("hosts",)} for c in N["clusters"]]
    return out


# ---- ACT ------------------------------------------------------------------------------------------
def vm_capacity_evidence(result, opts=None):
    """The numbers ACT reads, as text: overall, each cluster, each datastore - with the job's own runways."""
    o = opts or {}
    N = result.get("numbers") or {}
    ov = N.get("overall") or {}
    ids = {(i["kind"], i["name"]): i["id"] for i in result.get("items") or []}
    L = ["vSphere capacity, read %s UTC. Targets: memory and CPU used at most %s%% / %s%% with %s host(s) of each cluster failed; at "
         "most %s vCPUs per core; datastores amber at %s%%, red at %s%%. Growth: straight line over the last %s days of vCenter's daily "
         "statistics." % (N.get("now"), o.get("mem_target_pct", 80), o.get("cpu_target_pct", 80), o.get("failover_hosts", 1),
                          o.get("vcpu_per_core_max", 4), o.get("storage_warn_pct", 85), o.get("storage_crit_pct", 90), o.get("trend_days", 30)), ""]
    s = ov.get("storage") or {}
    L.append("%s OVERALL: %s clusters, %s hosts (%s usable), %s cores, %s VMs on (%s vCPUs, %s per core); CPU %s of %s used (%s, %s after host "
             "failure), growth %s; memory %s of %s used (%s, %s after host failure), growth %s, runway to target %s days; shared storage %s of %s "
             "used (%s, provisioned %s), growth %s, runway to %s%% %s days, to full %s days; VMs net %s per month; room for about %s more average VMs."
             % (ids.get(("overall", "all clusters"), "P1"), ov.get("clusters"), ov.get("hosts"), ov.get("hosts_usable"), ov.get("cores"), ov.get("vms_on"),
                ov.get("vcpus_on"), ov.get("vcpu_ratio"), ghz(ov.get("cpu_used")), ghz(ov.get("cpu")), pct_text(ov.get("cpu_pct")),
                pct_text(ov.get("cpu_n1_pct")), _per_month(ov.get("cpu_slope"), ghz), size(ov.get("mem_used")), size(ov.get("mem")),
                pct_text(ov.get("mem_pct")), pct_text(ov.get("mem_n1_pct")), _per_month(ov.get("mem_slope")), ov.get("mem_days"),
                size(s.get("used")), size(s.get("capacity")), pct_text(s.get("pct")), pct_text(s.get("prov_pct")), _per_month(s.get("slope")),
                o.get("storage_crit_pct", 90), s.get("days_crit"), s.get("days_full"), ov.get("net_per_month"), ov.get("fit")))
    L += ["", "CLUSTERS"]
    for n in N.get("clusters") or []:
        tm = n.get("mem_trend") or {}
        L.append("%s cluster %s (datacenter %s, status %s): hosts %s of %s usable, plan for %s failed; %s cores; CPU %s of %s (%s, %s after failure); "
                 "memory %s of %s (%s, %s after failure); vCPU per core %s; %s VMs on, average %s vCPU / %s configured / %s used memory; memory "
                 "growth %s (fit R2 %s over %s days), runway to target %s days; CPU runway %s days; room for %s more VMs (limit: %s); VMs added %s, "
                 "removed %s in the last %s days; HA %s; storage %s datastores, %s of %s used, runway to %s%% %s days; hosts recommended "
                 "(average host here: %s cores, %s memory): %s now, %s within %s months."
                 % (ids.get(("cluster", n["name"]), "?"), n["name"], n["datacenter"], n.get("status") or "ok", n["hosts_usable"], n["hosts_total"],
                    n["failover_hosts"], n["cores"], ghz(n["cpu_used"]), ghz(n["cpu"]), pct_text(n["cpu_pct"]), pct_text(n["cpu_n1_pct"]),
                    size(n["mem_used"]), size(n["mem"]), pct_text(n["mem_pct"]), pct_text(n["mem_n1_pct"]), n["vcpu_ratio"], n["vms_on"],
                    round((n["avg"] or {}).get("vcpu") or 0, 1), size((n["avg"] or {}).get("mem_configured")), size((n["avg"] or {}).get("mem_used")),
                    _per_month(tm.get("slope")) if tm else "unknown", tm.get("r2", "-"), tm.get("span", "-"), n["mem_days"], n["cpu_days"], n["fit"],
                    n["limit"] or "-", n["added"], n["removed"], o.get("events_days", 30), (n.get("ha") or {}).get("kind") or "off",
                    (n.get("storage") or {}).get("datastores"), size((n.get("storage") or {}).get("used")), size((n.get("storage") or {}).get("capacity")),
                    o.get("storage_warn_pct", 85), (n.get("storage") or {}).get("days_warn"), round(n.get("host_cores") or 0),
                    size(n.get("host_mem")), n.get("hosts_now"), n.get("hosts_later"), n.get("horizon_months")))
    L += ["", "DATASTORES (shared)"]
    for x in N.get("datastores") or []:
        if not (x.get("shared") or o.get("include_local_datastores")):
            continue
        t = x.get("trend") or {}
        L.append("%s datastore %s (clusters %s, status %s): %s of %s used (%s, provisioned %s), growth %s (fit R2 %s over %s days), runway to "
                 "%s%% %s days, to %s%% %s days, to full %s days."
                 % (ids.get(("datastore", x["name"]), "?"), x["name"], ", ".join(x["clusters"]), x.get("status") or "ok", size(x["used"]),
                    size(x["capacity"]), pct_text(x["pct"]), pct_text(x["prov_pct"]), _per_month(x.get("slope")), t.get("r2", "-"), t.get("span", "-"),
                    o.get("storage_warn_pct", 85), x["days_warn"], o.get("storage_crit_pct", 90), x["days_crit"], x["days_full"]))
    text = "\n".join(L)
    cap = int(o.get("max_chars") or 60000)
    return text if len(text) <= cap else text[:cap] + "\n[... cut at %d characters ...]" % cap


def vm_capacity_act_task(items, opts=None):
    o = opts or {}
    ids = ", ".join(i["id"] for i in items or [])
    return "\n".join([
        "vSphere capacity planning for an operations team.",
        "You cannot run any commands in this task, and none are needed: the evidence piped to you holds the capacity, usage, growth and "
        "runways the job computed from vCenter (its own daily statistics over the last %s days). Use those numbers; do not invent others."
        % o.get("trend_days", 30),
        "",
        "For each item (%s) give:" % ids,
        "- estimate: when it needs action (a date or a number of months), and what limit it hits first (memory, CPU, vCPUs, storage);",
        "- estimate_days: that as a whole number of days from today, or null when it needs no action within two years. Make your own "
        "estimate: the job's straight-line runway is in the evidence, and the report shows yours next to it - say why when they differ;",
        "- recommendation: what to do, concretely and in numbers - e.g. '1 to 2 more hosts recommended for PROD, bringing memory to about "
        "70% on average', 'about 4 TB more for datastore X', rebalance VMs, reclaim snapshots or powered-off VMs, review vCPU sizing - and "
        "by when. The evidence has the job's own count of recommended hosts: say so when you agree or why you differ;",
        "- wording: this is a government team. Do not write buy, purchase, order, procure, budget, cost, quarter or fiscal year: state "
        "needs as numbers of hosts, cores, GB or TB, and timing as dates, days or months;",
        "- reasoning: the numbers from the evidence that support it, in one or two sentences;",
        "- confidence: how sure you are, a whole number from 0 to 100: 80 or more = the trend is steady (good fit) and the evidence clear; "
        "50-79 = likely; under 50 = little history or a poor fit (then say what to watch).",
        "Mention it when the history is short or the fit is poor (jumps from VMs moved or snapshots deleted).",
        "",
        "End your answer with exactly one JSON object between a line BEGIN_ACT_ANALYSIS and a line END_ACT_ANALYSIS, nothing else between them:",
        "BEGIN_ACT_ANALYSIS",
        '{"overall": "three or four sentences: what is needed first, in hosts or TB, and when",',
        ' "items": [{"id": "P1", "estimate": "...", "estimate_days": 120, "recommendation": "...", "reasoning": "...", "confidence": 75}]}',
        "END_ACT_ANALYSIS",
        "Include every id. Use plain text inside the JSON strings (no markdown).",
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def vm_capacity_act_section(items, parsed=None, opts=None):
    """ACT's estimate as report sections (added to the report when ACT ran, or why it did not)."""
    o, pa = opts or {}, parsed or {}
    if not o.get("ran"):
        return [{"title": "ACT's estimate", "status": "warning", "lines": ["ACT did not run: %s" % (o.get("reason") or "unknown")]}]
    if not pa.get("parsed"):
        return [{"title": "ACT's estimate", "text": pa.get("error", ""), "lines": [x for x in str(o.get("raw") or "").splitlines() if x.strip()] or ["(empty)"]}]
    by = pa.get("by_id") or {}
    warn_d, crit_d = float(o.get("runway_warn_days", 90)), float(o.get("runway_crit_days", 30))
    rows, agree, differ = [], 0, 0
    for it in items or []:
        a = by.get(it["id"]) or {}
        c = a.get("confidence")
        cst = "" if c is None else ("ok" if c >= 80 else ("warning" if c >= 50 else "critical"))
        verdict, vst = compare(it.get("job_days"), a.get("estimate_days"), a.get("estimate_days") is not None or "estimate_days" in a)
        agree += vst == "ok"
        differ += vst == "warning"
        jd, ad = it.get("job_days"), a.get("estimate_days")
        rows.append(_row([it["id"], "%s %s" % (it["kind"], it["name"]),
                          (days_text(jd) + (" (%s)" % it["job_limit"] if it.get("job_limit") else "")) if jd is not None else "not growing",
                          days_text(ad) if ad is not None else ("no action within 2 years" if "estimate_days" in a else "?"),
                          verdict, a.get("estimate", ""), a.get("recommendation", ""), a.get("reasoning", ""), ("%d%%" % c) if c is not None else ""],
                         ["", "", runway_status(jd, warn_d, crit_d) if jd is not None else "",
                          runway_status(ad, warn_d, crit_d) if ad is not None else "", vst, "", "", "", cst],
                         it["status"] if it["status"] in ("critical", "warning") else ""))
    out = []
    if pa.get("overall"):
        out.append({"title": "GenAI (ACT): what to do first", "lines": [pa["overall"]]})
    out.append(_section("Math vs GenAI: runway estimates side by side",
                        "Two estimates of when each item needs action. Math: the job's straight line through vCenter's daily statistics "
                        "(the same as in the tables above). GenAI: ACT (an AI assistant) read those numbers and made its own estimate, with "
                        "judgement the line does not have (seasonality, jumps, what limit comes first). They agree when they are within 30 "
                        "days or 25%% of each other: %d agree, %d differ. Where they differ, read ACT's reasoning - and plan for the sooner "
                        "one. Confidence is ACT's own (green 80+, amber 50-79, red under 50)." % (agree, differ),
                        ["#", "Item", "Math (straight line)", "GenAI (ACT)", "Agreement", "ACT's estimate", "Recommendation", "Reasoning",
                         "Confidence"], rows, "", keep_order=True))
    return out


def compare(job_days, act_days, act_answered=True):
    """How the math and GenAI runways compare -> (text, status: ok = they agree, warning = they differ).
    More than two years away counts as no limit in sight (GenAI is asked about two years)."""
    if not act_answered:
        return "no GenAI estimate", ""
    job_days = None if job_days is not None and job_days > 730 else job_days
    act_days = None if act_days is not None and act_days > 730 else act_days
    if job_days is None and act_days is None:
        return "agree: no limit in sight", "ok"
    if job_days is None:
        return "only GenAI sees a limit", "warning"
    if act_days is None:
        return "only the math sees a limit", "warning"
    diff = act_days - job_days
    if abs(diff) <= max(30, 0.25 * max(job_days, act_days)):
        return "agree", "ok"
    return ("GenAI %d days sooner" % -diff) if diff < 0 else ("GenAI %d days later" % diff), "warning"


def vm_capacity_names(data):
    """Every name the evidence may hold (vCenter, datacenters, clusters, hosts and their short names,
    datastores): ACT replaces them with placeholders before anything reaches the model."""
    d = data or {}
    out = {d.get("vcenter", "")}
    for c in d.get("clusters") or []:
        out.update([c.get("name", ""), c.get("datacenter", "")])
        for h in c.get("hosts") or []:
            out.update([h.get("name", ""), str(h.get("name", "")).split(".")[0]])
    for x in d.get("datastores") or []:
        out.update([x.get("name", ""), x.get("pod", "")])
    return sorted(n for n in out if n and len(str(n)) > 2 and "," not in str(n))


class FilterModule(object):
    def filters(self):
        return {"vm_capacity_report": vm_capacity_report, "vm_capacity_evidence": vm_capacity_evidence,
                "vm_capacity_act_task": vm_capacity_act_task, "vm_capacity_act_section": vm_capacity_act_section,
                "capacity_compare": compare, "vm_capacity_names": vm_capacity_names,
                "capacity_trend": trend}
