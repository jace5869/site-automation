# -*- coding: utf-8 -*-
"""The VM right-sizing report (playbooks/vm_rightsizing_report.yml): every powered-on VM's vCPUs and memory next to
what vCenter's statistics say it uses (roles/vmware_vm/library/site_vmware_rightsize.py) - too big, about right or too
small, with a suggested size. The math is here and is the same every run; ACT (vm_rightsize_act) only reviews it.
Suggestions only: nothing is changed.

CPU is measured by the hypervisor itself, so its suggestions are firm. Memory is not: vCenter sees the memory a guest
touched recently (active memory), not what the guest needs - so a memory suggestion keeps a lot of headroom
(vm_rightsize_mem_headroom_pct), never cuts more than vm_rightsize_mem_max_cut_pct at once, and is marked as the less
certain one."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import math

DEFAULTS = {"min_days": 7, "cpu_target_pct": 70, "cpu_high_pct": 85, "ready_warn_pct": 5, "min_vcpus": 2, "cpu_max_cut_pct": 50,
            "min_vcpus_windows": 2, "mem_headroom_pct": 100, "mem_high_pct": 90, "min_mem_gb": 2, "min_mem_gb_windows": 4,
            "mem_max_cut_pct": 50, "min_change_pct": 25, "disk_warn_pct": 90, "taken_mb": 100, "max_rows": 500, "days": 30}
TOO_BIG, TOO_SMALL, RIGHT, UNKNOWN = "Too big", "Too small", "About right", "Not enough history"


def _o(opts):
    o = dict(DEFAULTS, **{k: v for k, v in (opts or {}).items() if v is not None})
    for k in DEFAULTS:
        o[k] = float(o[k])
    return o


def _short(name):
    n = str(name or "")
    return n if n.replace(".", "").isdigit() else n.split(".")[0]


def _num(x):
    """12.0 -> 12, 12.5 -> 12.5."""
    if x is None:
        return None
    x = round(float(x), 1)
    return int(x) if x == int(x) else x


def mem_size(gb):
    """A memory size people set: whole GB up to 4, then even GB, then multiples of 4 from 16."""
    gb = max(float(gb), 0.5)
    if gb <= 4:
        return int(math.ceil(gb))
    if gb <= 16:
        return int(math.ceil(gb / 2.0) * 2)
    return int(math.ceil(gb / 4.0) * 4)


def _pct(x):
    return "-" if x is None else ("%g%%" % round(float(x), 1))


def rightsize(vm, opts=None):
    """One VM -> {verdict, cpu: {now, suggested, change}, mem: {...}, why: [..], notes: [..], taken, disk, ...}.
    change: 'down', 'up' or '' (none)."""
    o = _o(opts)
    st = vm.get("stats") or {}
    cpus, mem_gb = int(vm.get("cpus") or 0), round(float(vm.get("memory_mb") or 0) / 1024.0, 1)
    win = bool(vm.get("windows"))
    out = {"name": vm.get("name", ""), "verdict": RIGHT, "why": [], "notes": [],
           "cpu": {"now": cpus, "suggested": cpus, "change": ""}, "mem": {"now": _num(mem_gb), "suggested": _num(mem_gb), "change": ""},
           "taken": False, "disk": None, "freed_vcpus": 0, "freed_gb": 0}

    # memory the host took back, a limit, and full guest disks: said whatever the history
    balloon, swapped = st.get("balloon_mb_max") or 0, st.get("swapped_mb_max") or 0
    limit = int(vm.get("mem_limit_mb") if vm.get("mem_limit_mb") is not None else -1)
    balloon, swapped = (x if x >= o["taken_mb"] else 0 for x in (balloon, swapped))      # a few MB now and then: not worth saying
    if balloon > 0 or swapped > 0 or 0 <= limit < (vm.get("memory_mb") or 0):
        out["taken"] = True
        bits = []
        if balloon > 0:
            bits.append("up to %s MB ballooned" % _num(balloon))
        if swapped > 0:
            bits.append("up to %s MB swapped by the host" % _num(swapped))
        if 0 <= limit < (vm.get("memory_mb") or 0):
            bits.append("a memory limit of %s GB is set, below its %s GB: the guest cannot use the rest" % (_num(limit / 1024.0), _num(mem_gb)))
        elif bits:
            bits.append("the ESXi host was short of memory (its other VMs, or the host itself): check the host before resizing")
        out["notes"].append("Memory taken back: " + "; ".join(bits) + ".")
    if int(vm.get("cpu_limit_mhz") if vm.get("cpu_limit_mhz") is not None else -1) >= 0:
        out["notes"].append("A CPU limit of %s MHz is set: its CPU use is capped there." % vm.get("cpu_limit_mhz"))
    disks = [d for d in vm.get("disks") or [] if d.get("capacity_gb")]
    if disks:
        full = max(disks, key=lambda d: 1 - float(d.get("free_gb") or 0) / float(d["capacity_gb"]))
        used = round(100.0 * (1 - float(full.get("free_gb") or 0) / float(full["capacity_gb"])), 1)
        out["disk"] = {"path": full.get("path", ""), "used_pct": used, "free_gb": full.get("free_gb"), "capacity_gb": full.get("capacity_gb"),
                       "full": used >= o["disk_warn_pct"]}

    days = float(st.get("days") or 0)
    if not st.get("samples") or days < o["min_days"] or st.get("cpu_p95") is None:
        out["verdict"] = UNKNOWN
        out["why"].append("%s day(s) of statistics in vCenter (vm_rightsize_min_days: %d)" % (_num(days), o["min_days"]))
        return out

    # CPU: the busiest 5% of the time left out (the 95th percentile), or the year's busiest day when that was busier
    p95, peak = float(st["cpu_p95"]), st.get("cpu_max")
    day = st.get("year_cpu_day")
    use = max(p95, float(day or 0))
    used_vcpus = cpus * use / 100.0
    floor = int(o["min_vcpus_windows"] if win else o["min_vcpus"])
    want = int(math.ceil(used_vcpus / (o["cpu_target_pct"] / 100.0) - 1e-9))
    half = int(math.ceil(cpus * (1 - o["cpu_max_cut_pct"] / 100.0) - 1e-9))
    need = max(want, floor, half, 1)
    need += need % 2 if need > 2 else 0                  # an even number from 4 up: how vCPUs are usually given
    ready = st.get("ready_p95")
    if use >= o["cpu_high_pct"]:
        sug = max(need, cpus + 1)
        sug += sug % 2 if sug > 2 else 0
        out["cpu"].update(suggested=sug, change="up")
        out["why"].append("CPU: busy - %s of its %d vCPUs (95th percentile)%s" % (
            _pct(p95), cpus, "" if day is None or day <= p95 else ", %s on its busiest day (%s)" % (_pct(day), st.get("year_cpu_date"))))
        if ready is not None and ready >= o["ready_warn_pct"]:
            out["notes"].append("It also waits for a physical CPU (CPU ready %s): check the ESXi host's load before adding vCPUs." % _pct(ready))
    elif need < cpus:
        out["cpu"].update(suggested=need, change="down")
        out["why"].append("CPU: about %s of %d vCPUs used (95th percentile %s%s)%s" % (
            _num(round(used_vcpus, 1)), cpus, _pct(p95), "" if day is None or day <= p95 else "; %s on its busiest day, %s" % (
                _pct(day), st.get("year_cpu_date")), "; at most %d%% fewer at once" % o["cpu_max_cut_pct"] if half > max(want, floor) else ""))
        if ready is not None and ready >= o["ready_warn_pct"]:
            out["notes"].append("It waits for a physical CPU (CPU ready %s): fewer vCPUs usually wait less." % _pct(ready))
    elif ready is not None and ready >= o["ready_warn_pct"]:
        out["notes"].append("It waits for a physical CPU (CPU ready %s): the ESXi host is busy." % _pct(ready))
    if out["cpu"]["change"] == "down" and peak is not None and cpus * float(peak) / 100.0 > out["cpu"]["suggested"]:
        out["notes"].append("At its peak it used about %s vCPUs, more than %d: short bursts would be slower." % (
            _num(round(cpus * float(peak) / 100.0, 1)), out["cpu"]["suggested"]))
    if day is not None and day > p95 and out["cpu"]["change"] != "up":
        out["notes"].append("Its busiest day of the year (%s, %s CPU) is sized for." % (st.get("year_cpu_date"), _pct(day)))

    # memory: active memory (what the guest touched lately) with a lot of headroom - the less certain suggestion
    mp95, mday = st.get("mem_p95"), st.get("year_mem_day")
    if mp95 is not None and mem_gb > 0:
        muse = max(float(mp95), float(mday or 0))
        active = mem_gb * muse / 100.0
        mfloor = o["min_mem_gb_windows"] if win else o["min_mem_gb"]
        if muse >= o["mem_high_pct"]:
            sug = mem_size(mem_gb * 1.25)
            out["mem"].update(suggested=_num(sug), change="up")
            out["why"].append("memory: active memory at %s of its %s GB (95th percentile)" % (_pct(muse), _num(mem_gb)))
        else:
            need, cut = active * (1 + o["mem_headroom_pct"] / 100.0), mem_gb * (1 - o["mem_max_cut_pct"] / 100.0)
            sug = mem_size(max(need, mfloor, cut))
            if sug <= mem_gb * (1 - o["min_change_pct"] / 100.0) and mem_gb - sug >= 2:
                out["mem"].update(suggested=_num(sug), change="down")
                out["why"].append("memory: about %s of %s GB active (95th percentile %s), plus %d%% headroom%s" % (
                    _num(round(active, 1)), _num(mem_gb), _pct(mp95), o["mem_headroom_pct"],
                    "; at most %d%% less at once" % o["mem_max_cut_pct"] if cut > max(need, mfloor) else ""))

    mpeak = st.get("mem_max")
    if out["mem"]["change"] == "down" and mpeak is not None and mem_gb * float(mpeak) / 100.0 > out["mem"]["suggested"]:
        out["notes"].append("At its peak about %s GB was active, more than %s GB: check it before giving less." % (
            _num(round(mem_gb * float(mpeak) / 100.0, 1)), out["mem"]["suggested"]))
    if "up" in (out["cpu"]["change"], out["mem"]["change"]):
        out["verdict"] = TOO_SMALL
    elif "down" in (out["cpu"]["change"], out["mem"]["change"]):
        out["verdict"] = TOO_BIG
    out["freed_vcpus"] = cpus - out["cpu"]["suggested"] if out["cpu"]["change"] == "down" else 0
    out["freed_gb"] = _num(mem_gb - out["mem"]["suggested"]) if out["mem"]["change"] == "down" else 0
    cold = [w for w, k, hot in (("vCPUs", "cpu", "cpu_hot_add"), ("memory", "mem", "mem_hot_add"))
            if out[k]["change"] == "up" and not vm.get(hot)]
    if cold:
        out["notes"].append("Adding %s needs the VM shut down (hot add is off)." % " and ".join(cold))
    return out


def vm_rightsize(vms, opts=None):
    """Every VM (site_vmware_rightsize's vms) -> [VM with its 'size': rightsize()], too small first, then the most
    freed, then by name."""
    out = [dict(v, size=rightsize(v, opts)) for v in vms or []]
    order = {TOO_SMALL: 0, TOO_BIG: 1, RIGHT: 2, UNKNOWN: 3}
    return sorted(out, key=lambda v: (order[v["size"]["verdict"]], -(v["size"]["freed_vcpus"] * 4 + v["size"]["freed_gb"]),
                                      str(v.get("vcenter", "")).lower(), str(v.get("name", "")).lower()))


def _where(v, multi):
    c = v.get("cluster") or (v.get("esxi_host") and "host " + _short(v.get("esxi_host"))) or "-"
    return "%s / %s" % (_short(v.get("vcenter")), c) if multi else c


def _change(part):
    if not part["change"]:
        return "%s" % part["now"]
    return "%s → %s" % (part["now"], part["suggested"])


def _used(st, k):
    return "%s / %s" % (_pct(st.get(k + "_p95")), _pct(st.get(k + "_max")))


def _size_row(v, multi):
    s, st = v["size"], v.get("stats") or {}
    return [v.get("name", ""), _where(v, multi), _change(s["cpu"]), _used(st, "cpu"), _pct(st.get("ready_p95")), _change(s["mem"]),
            _used(st, "mem"), "; ".join(s["why"] + [n for n in s["notes"] if not n.startswith("Memory taken back")])]


SIZE_COLS = ["VM", "Cluster", "vCPUs", "CPU used (95th % / peak)", "CPU ready", "Memory (GB)", "Active memory (95th % / peak)", "Why"]


def _table(title, text, rows, status, max_rows, columns=None, row_status=None, cell=None):
    sec = {"title": title, "columns": columns or SIZE_COLS, "rows": rows[:max_rows]}
    if status:
        sec["status"] = status
    if row_status:
        sec["row_status"] = row_status[:max_rows]
    if cell:
        sec["cell_status"] = cell[:max_rows]
    more = len(rows) - max_rows
    sec["text"] = text + ((" %d more not listed (vm_rightsize_max_rows: %d): the workbook has every VM." % (more, max_rows)) if more > 0 else "")
    return sec


def _cells(v, o):
    """Colour the cells that change (amber up, green down) and CPU ready from vm_rightsize_ready_warn_pct (amber)."""
    s, ready = v["size"], (v.get("stats") or {}).get("ready_p95")
    col = {"up": "warning", "down": "ok", "": ""}
    return ["", "", col[s["cpu"]["change"]], "", "warning" if ready is not None and ready >= o["ready_warn_pct"] else "",
            col[s["mem"]["change"]], "", ""]


def vm_rightsize_doc(result, sized, opts=None):
    """The report: {title, status, subtitle, summary, sections}. result: the module's answer (vcenters, skipped, notes,
    days, interval); sized: vm_rightsize."""
    o = _o(opts)
    r = result or {}
    mx = int(o["max_rows"])
    read = [s.get("name", "") for s in r.get("vcenters") or [] if s.get("ok")] or sorted(set(v.get("vcenter", "") for v in sized))
    multi = len(set(v.get("vcenter") for v in sized)) > 1
    by = {k: [v for v in sized if v["size"]["verdict"] == k] for k in (TOO_SMALL, TOO_BIG, RIGHT, UNKNOWN)}
    taken = [v for v in sized if v["size"]["taken"]]
    full = [v for v in sized if (v["size"]["disk"] or {}).get("full")]
    vcpus = sum(v["size"]["freed_vcpus"] for v in sized)
    gb = _num(sum(v["size"]["freed_gb"] for v in sized)) or 0
    grow_c = sum(v["size"]["cpu"]["suggested"] - v["size"]["cpu"]["now"] for v in by[TOO_SMALL] if v["size"]["cpu"]["change"] == "up")
    grow_m = _num(sum(v["size"]["mem"]["suggested"] - v["size"]["mem"]["now"] for v in by[TOO_SMALL] if v["size"]["mem"]["change"] == "up")) or 0
    sk = r.get("not_checked") or {}
    days, iv = int(r.get("days") or o["days"]), int(r.get("interval") or 7200)
    avg = {7200: "2-hour averages", 1800: "30-minute averages", 300: "5-minute averages"}.get(iv, "%d-second averages" % iv)

    sections = []
    if r.get("notes"):
        sections.append({"title": "What vCenter's statistics could not give", "status": "warning", "lines": list(r["notes"])})
    if by[TOO_SMALL]:
        sections.append(_table("Too small", "CPU busy most of the time, or active memory near all it has. More vCPUs or memory "
                               "suggested (amber).%s" % (" Together: %d vCPUs and %s GB more." % (grow_c, grow_m) if grow_c or grow_m else ""),
                               [_size_row(v, multi) for v in by[TOO_SMALL]], "warning", mx,
                               row_status=["warning"] * len(by[TOO_SMALL]), cell=[_cells(v, o) for v in by[TOO_SMALL]]))
    if taken:
        rows = [[v.get("name", ""), _where(v, multi), _short(v.get("esxi_host")) or "-",
                 "%s MB" % _num((v.get("stats") or {}).get("balloon_mb_max") or 0),
                 "%s MB" % _num((v.get("stats") or {}).get("swapped_mb_max")) if (v.get("stats") or {}).get("swapped_mb_max") is not None else "-",
                 ("%s GB" % _num(v["mem_limit_mb"] / 1024.0)) if int(v.get("mem_limit_mb") if v.get("mem_limit_mb") is not None else -1) >= 0 else "none",
                 " ".join(n for n in v["size"]["notes"] if n.startswith("Memory taken back"))] for v in taken]
        sections.append(_table("Memory taken back by the host",
                               "The ESXi host took memory back from these VMs (ballooning, swapping) or a limit caps them: they run "
                               "slower than their size says. Not a sizing problem of the VM: its host, or the limit.",
                               rows, "warning", mx, columns=["VM", "Cluster", "ESXi host", "Ballooned (peak)", "Swapped (peak)",
                                                             "Memory limit", "Why"], row_status=["warning"] * len(rows)))
    if by[TOO_BIG]:
        sections.append(_table("Too big", "More vCPUs or memory than they use: the suggested size (green) still leaves room - "
                               "CPU sized to %d%% busy, memory to its active memory plus %d%%. Together they free %d vCPUs and "
                               "%s GB of memory for other VMs." % (o["cpu_target_pct"], o["mem_headroom_pct"], vcpus, gb),
                               [_size_row(v, multi) for v in by[TOO_BIG]], "", mx, cell=[_cells(v, o) for v in by[TOO_BIG]]))
    if full:
        rows = [[v.get("name", ""), _where(v, multi), v["size"]["disk"]["path"], "%s%%" % v["size"]["disk"]["used_pct"],
                 "%s GB" % _num(v["size"]["disk"]["free_gb"]), "%s GB" % _num(v["size"]["disk"]["capacity_gb"])] for v in full]
        sections.append(_table("Guest disks nearly full", "As VMware Tools reports them (%d%% used or more: vm_rightsize_disk_warn_pct)."
                               % o["disk_warn_pct"], rows, "warning", mx, columns=["VM", "Cluster", "Disk", "Used", "Free", "Size"],
                               row_status=["warning"] * len(rows)))
    if by[UNKNOWN]:
        rows = [[v.get("name", ""), _where(v, multi), "%s" % v["size"]["cpu"]["now"], "%s" % v["size"]["mem"]["now"],
                 "; ".join(v["size"]["why"])] for v in by[UNKNOWN]]
        sections.append(_table(UNKNOWN, "Too little of vCenter's statistics to size them (new VMs, or moved in from another "
                               "vCenter): checked again next run.", rows, "", mx, columns=["VM", "Cluster", "vCPUs", "Memory (GB)", "Why"]))
    if by[RIGHT]:
        sections.append(_table(RIGHT, "Sized for what they use.", [_size_row(v, multi) for v in by[RIGHT]], "", mx,
                               cell=[_cells(v, o) for v in by[RIGHT]]))
    sections.append({"title": "How the suggestions are worked out", "lines": [
        "From vCenter's own statistics of the last %d days (%s) - the hypervisor's measurements, nothing from inside the guests - "
        "and each VM's busiest day of the last year (daily averages)." % (days, avg),
        "CPU: the busiest 5%% of the time is left out (the 95th percentile), or the year's busiest day is used when it was busier; "
        "vCPUs are suggested so that use would be about %d%%, never more than %d%% fewer at once, at least %d (Windows %d). %d%% "
        "or more is too small." % (o["cpu_target_pct"], o["cpu_max_cut_pct"], o["min_vcpus"], o["min_vcpus_windows"], o["cpu_high_pct"]),
        "CPU ready is the time a VM waited for a physical CPU: %d%% or more is said. It often means too many vCPUs, or a busy host."
        % o["ready_warn_pct"],
        "Memory (the less certain one): vCenter sees the memory a guest touched in the last minutes (active memory), not what it needs "
        "- file caches and databases want more. So the suggestion is its active memory plus %d%%, never more than %d%% less at once, "
        "at least %d GB (Windows %d), and only when it is %d%% and 2 GB smaller or more. Active memory at %d%% or more is too small."
        % (o["mem_headroom_pct"], o["mem_max_cut_pct"], o["min_mem_gb"], o["min_mem_gb_windows"], o["min_change_pct"], o["mem_high_pct"]),
        ] + list(r.get("info") or []) + [
        "Suggestions only: nothing was changed. Check a VM's own peaks (month end, patching, backups) before resizing it; removing "
        "vCPUs or memory needs the VM shut down."]})

    status = "warning" if by[TOO_SMALL] or taken or full or r.get("notes") else "ok"
    n = len(sized)
    summary = [{"label": "VMs checked", "value": n},
               {"label": TOO_BIG, "value": len(by[TOO_BIG])},
               {"label": TOO_SMALL, "value": len(by[TOO_SMALL]), "status": "warning" if by[TOO_SMALL] else "ok"},
               {"label": RIGHT, "value": len(by[RIGHT])},
               {"label": "vCPUs to free", "value": vcpus}, {"label": "Memory to free (GB)", "value": gb}]
    if by[UNKNOWN]:
        summary.append({"label": UNKNOWN, "value": len(by[UNKNOWN])})
    if taken:
        summary.append({"label": "Memory taken back", "value": len(taken), "status": "warning"})
    if full:
        summary.append({"label": "Disks nearly full", "value": len(full), "status": "warning"})
    return {"title": "VM right-sizing: %d too big, %d too small of %d VM%s" % (len(by[TOO_BIG]), len(by[TOO_SMALL]), n, "" if n == 1 else "s"),
            "status": status,
            "subtitle": "vCPUs and memory next to what vCenter's statistics of the last %d days say each VM uses - %s; %s - "
                        "suggestions only, nothing was changed" % (
                            days, ", ".join(_short(x) for x in read) or "no vCenter",
                            "%d powered off and %d templates not checked" % (sk.get("off", 0), sk.get("templates", 0))),
            "summary": summary, "sections": sections}


def vm_rightsize_xlsx(sized, opts=None):
    """The email's workbook: every VM with every number, and every guest disk."""
    rows, disks = [], []
    for v in sorted(sized or [], key=lambda v: (str(v.get("vcenter", "")).lower(), str(v.get("cluster", "")).lower(), str(v.get("name", "")).lower())):
        s, st = v["size"], v.get("stats") or {}
        d = s["disk"] or {}
        rows.append([v.get("vcenter", ""), v.get("datacenter", ""), v.get("cluster", ""), v.get("esxi_host", ""), v.get("name", ""),
                     v.get("guest_os", ""), s["verdict"], s["cpu"]["now"], s["cpu"]["suggested"], st.get("cpu_avg"), st.get("cpu_p95"),
                     st.get("cpu_max"), st.get("ready_p95"), st.get("year_cpu_day"), st.get("year_cpu_date") or "", s["mem"]["now"],
                     s["mem"]["suggested"], st.get("mem_p95"), st.get("mem_max"), st.get("year_mem_day"),
                     _num((st.get("consumed_mb_avg") or 0) / 1024.0) if st.get("consumed_mb_avg") is not None else None,
                     st.get("balloon_mb_max"), st.get("swapped_mb_max"), st.get("days"), d.get("path", ""), d.get("used_pct"),
                     "; ".join(s["why"] + s["notes"])])
        for x in v.get("disks") or []:
            cap = float(x.get("capacity_gb") or 0)
            disks.append([v.get("vcenter", ""), v.get("name", ""), x.get("path", ""), x.get("capacity_gb"), x.get("free_gb"),
                          round(100.0 * (1 - float(x.get("free_gb") or 0) / cap), 1) if cap else None])
    return [{"name": "Right-sizing",
             "columns": ["vCenter", "Datacenter", "Cluster", "ESXi host", "VM", "Guest OS", "Verdict", "vCPUs", "Suggested vCPUs",
                         "CPU avg %", "CPU 95th %", "CPU peak %", "CPU ready 95th %", "Busiest day CPU %", "Busiest day", "Memory (GB)",
                         "Suggested memory (GB)", "Active memory 95th %", "Active memory peak %", "Busiest day memory %",
                         "Consumed memory avg (GB)", "Ballooned peak (MB)", "Swapped peak (MB)", "Days of history", "Fullest disk",
                         "Fullest disk used %", "Why"],
             "rows": rows},
            {"name": "Guest disks", "columns": ["vCenter", "VM", "Disk", "Size (GB)", "Free (GB)", "Used %"], "rows": disks}]


def vm_rightsize_change(v):
    """One VM's suggestion, short (the job's artifacts)."""
    s = v["size"]
    return {"vcenter": v.get("vcenter", ""), "name": v.get("name", ""), "verdict": s["verdict"],
            "vcpus": [s["cpu"]["now"], s["cpu"]["suggested"]], "memory_gb": [s["mem"]["now"], s["mem"]["suggested"]]}


def vm_rightsize_lines(sized):
    """A line per VM to change, for the job's output: 'web01 (vc01): Too big - vCPUs 8 → 4, memory 32 → 16 GB'."""
    multi = len(set(v.get("vcenter") for v in sized or [])) > 1
    return ["%s%s: %s - vCPUs %s, memory %s GB" % (v.get("name", ""), " (%s)" % _short(v.get("vcenter")) if multi else "", v["size"]["verdict"],
                                                   _change(v["size"]["cpu"]), _change(v["size"]["mem"]))
            for v in sized or [] if v["size"]["verdict"] in (TOO_SMALL, TOO_BIG)]


# ---- ACT ------------------------------------------------------------------------------------------
def vm_rightsize_items(sized, opts=None):
    """The suggestions ACT reviews: too small first, then too big (the most freed first), at most max_items. [{id, name, ...}]"""
    n = int((opts or {}).get("max_items") or 40)
    pick = [v for v in sized or [] if v["size"]["verdict"] in (TOO_SMALL, TOO_BIG)][:n]
    return [{"id": "P%d" % (i + 1), "name": v.get("name", ""), "vcenter": v.get("vcenter", ""), "verdict": v["size"]["verdict"],
             "status": "warning" if v["size"]["verdict"] == TOO_SMALL else ""} for i, v in enumerate(pick)]


def vm_rightsize_evidence(items, sized, opts=None):
    """What ACT reads: the rules, then one line per item with its numbers and the job's suggestion."""
    o = _o(opts)
    by = {(v.get("vcenter", ""), v.get("name", "")): v for v in sized or []}
    L = ["VM right-sizing from vCenter's statistics of the last %d days (the hypervisor's measurements; no data from inside the "
         "guests). Percentages are of what the VM has. CPU sized to about %d%% busy at the 95th percentile (or the year's busiest "
         "day); memory = active memory (what the guest touched in the last minutes - it understates real need) plus %d%%, never "
         "more than %d%% less at once." % (o["days"], o["cpu_target_pct"], o["mem_headroom_pct"], o["mem_max_cut_pct"]), ""]
    for it in items or []:
        v = by.get((it.get("vcenter", ""), it.get("name", "")))
        if v is None:
            continue
        s, st = v["size"], v.get("stats") or {}
        L.append("%s VM %s (%s; guest %s): job says %s. vCPUs %s -> %s; CPU avg %s, 95th %s, peak %s, ready 95th %s, busiest day "
                 "%s (%s). Memory %s GB -> %s GB; active 95th %s, peak %s, busiest day %s; consumed avg %s MB; ballooned peak %s MB; "
                 "swapped peak %s MB; %s days of history. Job's reasons: %s"
                 % (it["id"], v.get("name", ""), v.get("cluster") or "-", v.get("guest_os") or "-", s["verdict"], s["cpu"]["now"],
                    s["cpu"]["suggested"], _pct(st.get("cpu_avg")), _pct(st.get("cpu_p95")), _pct(st.get("cpu_max")),
                    _pct(st.get("ready_p95")), _pct(st.get("year_cpu_day")), st.get("year_cpu_date") or "-", s["mem"]["now"],
                    s["mem"]["suggested"], _pct(st.get("mem_p95")), _pct(st.get("mem_max")), _pct(st.get("year_mem_day")),
                    st.get("consumed_mb_avg"), st.get("balloon_mb_max"), st.get("swapped_mb_max"), st.get("days"),
                    "; ".join(s["why"] + s["notes"]) or "-"))
    return "\n".join(L)


def vm_rightsize_act_task(items, opts=None):
    o = opts or {}
    ids = ", ".join(i["id"] for i in items or [])
    return "\n".join([
        "VM right-sizing review for an operations team.",
        "You cannot run any commands in this task, and none are needed: the evidence piped to you holds each VM's size, what "
        "vCenter's statistics say it used, and the job's suggested size. Use those numbers; do not invent others.",
        "",
        "For each item (%s) give:" % ids,
        "- recommendation: start with 'Agree:' or 'Adjust:' and then the size you would set, in vCPUs and GB, and how safe the change "
        "is. Be careful with memory: vCenter's active memory understates what databases, Java and file caches need - say so when the "
        "guest OS or the name suggests one;",
        "- reasoning: the numbers from the evidence that support it, in one or two sentences;",
        "- confidence: how sure you are, a whole number from 0 to 100.",
        "Do not write buy, purchase, order, procure, budget or cost: state sizes as vCPUs and GB.",
        "",
        "End your answer with exactly one JSON object between a line BEGIN_ACT_ANALYSIS and a line END_ACT_ANALYSIS, nothing else between them:",
        "BEGIN_ACT_ANALYSIS",
        '{"overall": "three or four sentences: patterns you see, and which changes are the safest to make first",',
        ' "items": [{"id": "P1", "recommendation": "Agree: ...", "reasoning": "...", "confidence": 75}]}',
        "END_ACT_ANALYSIS",
        "Include every id. Use plain text inside the JSON strings (no markdown).",
    ] + (["", str(o["extra"])] if o.get("extra") else []))


def vm_rightsize_act_section(items, sized, parsed=None, opts=None):
    """ACT's review as report sections (or why it did not run)."""
    o, pa = opts or {}, parsed or {}
    if not o.get("ran"):
        return [{"title": "ACT's review", "status": "warning", "lines": ["ACT did not run: %s" % (o.get("reason") or "unknown")]}]
    if not pa.get("parsed"):
        return [{"title": "ACT's review", "text": pa.get("error", ""),
                 "lines": [x for x in str(o.get("raw") or "").splitlines() if x.strip()] or ["(empty)"]}]
    by = pa.get("by_id") or {}
    sz = {(v.get("vcenter", ""), v.get("name", "")): v for v in sized or []}
    rows, cells, agree = [], [], 0
    for it in items or []:
        a = by.get(it["id"]) or {}
        v = sz.get((it.get("vcenter", ""), it.get("name", "")))
        rec = a.get("recommendation", "")
        ok = rec.lower().startswith("agree")
        agree += ok
        c = a.get("confidence")
        job = ("%s; %s" % ("vCPUs " + _change(v["size"]["cpu"]), "memory " + _change(v["size"]["mem"]) + " GB")) if v else ""
        rows.append([it["id"], it.get("name", ""), it.get("verdict", ""), job, rec or "?", a.get("reasoning", ""),
                     ("%d%%" % c) if c is not None else ""])
        cells.append(["", "", "", "", "ok" if ok else ("warning" if rec else ""), "",
                      "" if c is None else ("ok" if c >= 80 else ("warning" if c >= 50 else "critical"))])
    out = []
    if pa.get("overall"):
        out.append({"title": "GenAI (ACT): what to change first", "lines": [pa["overall"]]})
    out.append({"title": "ACT's review of each suggestion",
                "text": "ACT (an AI assistant) read the same numbers and reviewed the job's suggestion: %d of %d agreed. Where it "
                        "adjusts one, read its reasoning; the job's own math is in the tables below. Confidence is ACT's own (green "
                        "80+, amber 50-79, red under 50)." % (agree, len(items or [])),
                "columns": ["#", "VM", "Job says", "Job's size", "ACT's recommendation", "Reasoning", "Confidence"],
                "rows": rows, "cell_status": cells})
    return out


def vm_rightsize_names(vms):
    """Every name ACT's evidence may hold (VMs, clusters, hosts, vCenters, datacenters): masked before the model sees them."""
    out = set()
    for v in vms or []:
        for k in ("name", "cluster", "datacenter", "vcenter", "esxi_host", "hostname"):
            x = str(v.get(k) or "")
            out.update([x, _short(x)])
    return sorted(n for n in out if n and len(n) > 2 and "," not in n)


class FilterModule(object):
    def filters(self):
        return {"vm_rightsize": vm_rightsize, "vm_rightsize_doc": vm_rightsize_doc, "vm_rightsize_xlsx": vm_rightsize_xlsx,
                "vm_rightsize_change": vm_rightsize_change, "vm_rightsize_lines": vm_rightsize_lines,
                "vm_rightsize_items": vm_rightsize_items, "vm_rightsize_evidence": vm_rightsize_evidence,
                "vm_rightsize_act_task": vm_rightsize_act_task, "vm_rightsize_act_section": vm_rightsize_act_section,
                "vm_rightsize_names": vm_rightsize_names}
