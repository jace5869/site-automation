"""The portal's ops-center wall (/portal/wall, docs/SITE_PORTAL.md "The wall"): the one model every style of the page draws.

Its inputs: the live collector's view (wall_live.Collector.view(): each vCenter's last good read and the short history of
host and VM CPU and memory), the NetApp page's newest run in the report database (netapp_live.Database.read()), and the
settings (the thresholds). Its answer: the wall conditions open now and cleared in the last 4 hours, each area's feeds with
"as of", and the levels the gauges show. The rules, one place for all of them:
  - a condition is on the wall only when a wall rule says so (hosts not responding, a datastore 85 / 95 % used, CPU or memory
    90 % or more for 15 min or longer ...); routine alarms and short spikes never are;
  - freshness is per feed: LATE after 3 min, STALE after 10 min; a stale feed's values are the last known ones, never green;
  - a condition keeps its start while it stays open (Tracker); one that goes away is "cleared" for 4 hours (the Map's timeline).
Standard library only; no I/O (the caller reads, this works it out), so the tests drive it with made-up reads."""
import collections
import fnmatch
import re

LATE_S = 180                     # amber (LATE) after 3 min
STALE_S = 600                    # STALE (not current, never green) after 10 min
KEEP_CLEARED_S = 4 * 3600        # cleared conditions on the Map's timeline
AUTO_SITE = r"^([A-Za-z]{4}[0-9]{2})"
DOWN_STATES = ("notResponding", "disconnected")

AREAS = (
    {"id": "storage", "name": "Storage", "sub": "NetApp", "team": "Storage team"},
    {"id": "vmware", "name": "VMware", "sub": "vCenter", "team": "VMware team"},
    {"id": "vdi", "name": "VDI", "sub": "Horizon", "team": "VDI team"},
    {"id": "ad", "name": "Active Directory", "short": "AD", "sub": "", "team": "AD team"},
    {"id": "tickets", "name": "Outages & problems", "short": "Outages & PRB", "sub": "ServiceNow", "team": "Service desk"},
)
BUILT = ("storage", "vmware")    # stage 1: the others are grey tiles "not on the wall yet"
NOT_ON_WALL = {
    "storage": "EMS informational events, snapshot counts, autosupport, lagging but healthy SnapMirror.",
    "vmware": "vCenter informational alarms, short spikes (under the sustained window), snapshot age, tools out of date.",
    "vdi": "not on the wall yet.", "ad": "not on the wall yet.", "tickets": "not on the wall yet.",
}

DEFAULTS = {"ds_warn": 85.0, "ds_crit": 95.0, "aggr_warn": 85.0, "aggr_crit": 97.0, "high_pct": 90.0, "high_minutes": 15,
            "seconds": 60, "maintenance_expected": [], "vm_heartbeat": True, "site_pattern": "", "site_places": {},
            "late": LATE_S, "stale": STALE_S}


def opts_of(o):
    """the settings with their defaults; a wrong value falls back to the default (the deploy checks them first)"""
    out = dict(DEFAULTS)
    for k, v in (o or {}).items():
        if k not in DEFAULTS or v is None:
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                out[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(d, float):
                out[k] = float(v)
            elif isinstance(d, int):
                out[k] = int(v)
            elif isinstance(d, list):
                out[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else []
            elif isinstance(d, dict):
                out[k] = dict(v) if isinstance(v, dict) else {}
            else:
                out[k] = str(v)
        except (TypeError, ValueError):
            pass
    if not 0 < out["ds_warn"] < out["ds_crit"] <= 100:
        out["ds_warn"], out["ds_crit"] = DEFAULTS["ds_warn"], DEFAULTS["ds_crit"]
    if not 0 < out["aggr_warn"] < out["aggr_crit"] <= 100:
        out["aggr_warn"], out["aggr_crit"] = DEFAULTS["aggr_warn"], DEFAULTS["aggr_crit"]
    out["high_pct"] = min(100.0, max(1.0, out["high_pct"]))
    out["high_minutes"] = max(1, min(240, out["high_minutes"]))
    out["seconds"] = max(30, min(3600, out["seconds"]))
    return out


def kinds(o):
    """the wall conditions with the thresholds in force: what lights the wall, said on "What the wall watches"."""
    hp, hm = _n(o["high_pct"]), o["high_minutes"]
    return {
        "vc": {"area": "vmware", "sev": "crit", "label": "vCenter not answering, or its login failed", "grp": "hostvc"},
        "host": {"area": "vmware", "sev": "crit", "label": "Host not responding or disconnected", "grp": "hostvc"},
        "maint": {"area": "vmware", "sev": "warn", "label": "Host in maintenance mode (not listed as expected)", "grp": "hostvc"},
        "dsi": {"area": "vmware", "sev": "crit", "label": "Datastore inaccessible", "grp": "dsvm"},
        "vmhung": {"area": "vmware", "sev": "crit", "label": "VM powered on with no heartbeat (red)", "grp": "dsvm"},
        "cpu": {"area": "vmware", "sev": "warn", "label": "Host or VM CPU %s %% or more for %d min or more" % (hp, hm), "grp": "load"},
        "mem": {"area": "vmware", "sev": "warn", "label": "Host or VM memory %s %% or more for %d min or more" % (hp, hm), "grp": "load"},
        "dsfull": {"area": "storage", "sev": "crit", "label": "Datastore %s %% used or more" % _n(o["ds_crit"]), "grp": "ds"},
        "dswarn": {"area": "storage", "sev": "warn", "label": "Datastore %s %% used or more" % _n(o["ds_warn"]), "grp": "ds"},
        "aggrfull": {"area": "storage", "sev": "crit", "label": "Aggregate %s %% used or more" % _n(o["aggr_crit"]), "grp": "aggr"},
        "aggrwarn": {"area": "storage", "sev": "warn", "label": "Aggregate %s %% used or more" % _n(o["aggr_warn"]), "grp": "aggr"},
        "voloff": {"area": "storage", "sev": "crit", "label": "Volume or LUN offline", "grp": "aggr"},
        "node": {"area": "storage", "sev": "crit", "label": "Storage node down or cluster not answering", "grp": "aggr"},
        "sm": {"area": "storage", "sev": "warn", "label": "SnapMirror relationship broken or unhealthy", "grp": "sm"},
    }


def _n(x):
    return ("%d" % x) if float(x) == int(x) else ("%.1f" % x)


def fstate(age, o=None):
    """a feed's age (s, None = never read) -> ok | late | stale"""
    late, stale = (o or DEFAULTS)["late"], (o or DEFAULTS)["stale"]
    if age is None:
        return "stale"
    return "stale" if age > stale else "late" if age > late else "ok"


def level(v, warn, crit):
    return "crit" if v >= crit else "warn" if v >= warn else "ok"


def site_rule(pattern):
    try:
        rx = re.compile(str(pattern or "").strip() or AUTO_SITE)
    except re.error:
        return None
    return rx if rx.groups >= 1 else None


def site_of(rx, *names):
    for n in names:
        m = rx.search(str(n or "")) if rx is not None else None
        if m and m.group(1):
            return m.group(1).upper()
    return ""


# ---- sustained high: from the collector's own samples ------------------------------------------------------------------
def run_of(samples, thr, seconds):
    """samples [(t, pct)], oldest first -> (now value, start of the run at or over thr that reaches the newest sample, or None).
    A gap longer than 2.5 polls breaks a run (nobody knows what it was then)."""
    if not samples:
        return None, None
    t_last, v_last = samples[-1]
    if v_last is None or v_last < thr:
        return v_last, None
    start, prev = t_last, t_last
    for t, v in reversed(samples[:-1]):
        if v is None or v < thr or prev - t > 2.5 * seconds:
            break
        start, prev = t, t
    return v_last, start


def held(start, t_last, seconds):
    """how long a run has held (s): from its first sample to its newest, plus the poll that newest sample stands for"""
    return (t_last - start) + seconds


def spark(samples, now, minutes=60):
    """the last hour, one value a minute (None where there was no sample), for the sparkline"""
    out = [None] * minutes
    for t, v in samples:
        i = minutes - 1 - int((now - t) // 60)
        if 0 <= i < minutes and v is not None:
            out[i] = round(float(v), 1)
    return out


# ---- the tracker: when a condition started, which ones cleared ---------------------------------------------------------------
class Tracker(object):
    """Conditions by their id: the start stays while one is open (the earliest seen); one that goes away is kept as cleared for
    KEEP_CLEARED_S. Memory only: a restart of the portal starts again (said on the page as "since" = first seen)."""

    def __init__(self, keep=KEEP_CLEARED_S):
        self.keep, self.open, self.cleared = keep, {}, collections.OrderedDict()

    def update(self, conds, now):
        seen = set()
        for c in conds:
            cid = c["id"]
            seen.add(cid)
            old = self.open.get(cid)
            start = c.get("start") or now
            if old is not None:
                start = min(old["start"], start)
            c["start"] = start
            self.open[cid] = dict(c)
            self.cleared.pop(cid, None)
        for cid in [k for k in self.open if k not in seen]:
            c = self.open.pop(cid)
            c["end"] = now
            self.cleared[cid] = c
        for cid in [k for k, c in self.cleared.items() if now - c["end"] > self.keep]:
            self.cleared.pop(cid)
        return conds + [dict(c) for c in self.cleared.values()]


# ---- the model -----------------------------------------------------------------------------------------------------------------
def _vc_feed(src):
    return "vc:" + str(src.get("name") or "")


def vmware_part(view, o, now):
    """the vCenters' last good reads -> (part, conds, feeds)"""
    conds, feeds = [], {}
    part = {"configured": False, "vcenters": [], "clusters": [], "hosts": [], "datastores": [], "hot": [], "building": [], "vms": 0}
    srcs = [dict(s, key=k) for k, s in ((view or {}).get("sources") or {}).items() if s.get("kind") == "vcenter"]
    hist = (view or {}).get("history") or {}
    if not srcs:
        return part, conds, feeds
    part["configured"] = True
    rx = site_rule(o["site_pattern"])
    thr, win, sec = o["high_pct"], o["high_minutes"] * 60, o["seconds"]
    for src in sorted(srcs, key=lambda s: str(s.get("name"))):
        name, fk = str(src.get("name") or ""), _vc_feed(src)
        state = src.get("state") or "never"
        feeds[fk] = {"name": "vCenter " + name, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % sec, "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        part["vcenters"].append({"name": name, "up": state == "ok", "state": state, "why": src.get("why") or "", "feed": fk})
        if state in ("auth", "error"):
            why = "login failed - check the credential" if state == "auth" else (src.get("why") or "not answering")
            conds.append({"id": "vc:" + name, "kind": "vc", "t": "vCenter %s %s" % (name, "login failed" if state == "auth" else "not answering"),
                          "obj": why, "start": src.get("fail_since") or now, "feed": "", "vc": name, "site": site_of(rx, name)})
        snap = src.get("snap") or {}
        hosts = snap.get("hosts") or []
        by_cluster = collections.OrderedDict()
        for h in hosts:
            by_cluster.setdefault(h.get("cluster") or "(standalone)", []).append(h)
        for cn, hs in by_cluster.items():
            up = [h for h in hs if h.get("state") not in DOWN_STATES]
            cpus = [h["cpu_pct"] for h in up if h.get("cpu_pct") is not None]
            mems = [h["mem_pct"] for h in up if h.get("mem_pct") is not None]
            part["clusters"].append({"n": cn, "vc": name, "feed": fk, "up": len(up), "of": len(hs),
                                     "cpu": round(sum(cpus) / len(cpus), 1) if cpus else None,
                                     "mem": round(sum(mems) / len(mems), 1) if mems else None,
                                     "down": [h["name"] for h in hs if h.get("state") in DOWN_STATES],
                                     "maint": [h["name"] for h in hs if h.get("maint")], "site": site_of(rx, cn, hs[0].get("name"))})
        for h in hosts:
            hn, st = h.get("name") or "", h.get("state") or ""
            down = st in DOWN_STATES
            row = {"n": hn, "vc": name, "cluster": h.get("cluster") or "", "state": st, "maint": bool(h.get("maint")), "feed": fk,
                   "cpu": h.get("cpu_pct"), "mem": h.get("mem_pct"), "vms": sorted(h.get("vms") or [])[:200], "nvms": len(h.get("vms") or []),
                   "ds": sorted(h.get("datastores") or []), "power": h.get("power") or "", "site": site_of(rx, h.get("cluster"), hn), "st": "ok"}
            if down:
                row["st"] = "crit"
                conds.append({"id": "host:%s:%s" % (name, hn), "kind": "host", "t": "%s %s" % (hn, "not responding" if st == "notResponding" else "disconnected"),
                              "obj": "cluster %s · %d VMs on it" % (row["cluster"] or "-", row["nvms"]), "feed": fk, "vc": name, "host": hn,
                              "cluster": row["cluster"], "site": row["site"]})
            elif row["maint"] and not any(fnmatch.fnmatch(hn.lower(), p.lower()) for p in o["maintenance_expected"]):
                row["st"] = "warn"
                conds.append({"id": "maint:%s:%s" % (name, hn), "kind": "maint", "t": "%s in maintenance mode" % hn,
                              "obj": "cluster %s · not listed in portal_wall_maintenance_expected" % (row["cluster"] or "-"), "feed": fk, "vc": name,
                              "host": hn, "cluster": row["cluster"], "site": row["site"]})
            part["hosts"].append(row)
        dsrows = []
        for d in snap.get("datastores") or []:
            cap, free = int(d.get("capacity") or 0), int(d.get("free") or 0)
            pct = round(100.0 * (cap - free) / cap, 1) if cap else None
            r = {"n": d.get("name") or "", "vc": name, "feed": fk, "type": d.get("type") or "", "size": cap, "free": free, "v": pct,
                 "accessible": d.get("accessible") is not False, "hosts": sorted(d.get("hosts") or []), "nvms": int(d.get("vms") or 0)}
            r["site"] = site_of(rx, r["n"], (r["hosts"] or [""])[0])
            dsrows.append(r)
            if not r["accessible"]:
                conds.append({"id": "dsi:%s:%s" % (name, r["n"]), "kind": "dsi", "t": "Datastore %s inaccessible" % r["n"],
                              "obj": "mounted on %d hosts · %d VMs" % (len(r["hosts"]), r["nvms"]), "feed": fk, "vc": name, "ds": r["n"], "site": r["site"]})
            elif pct is not None and pct >= o["ds_warn"]:
                k = "dsfull" if pct >= o["ds_crit"] else "dswarn"
                conds.append({"id": "ds:%s:%s" % (name, r["n"]), "kind": k, "t": "%s %s %% full" % (r["n"], _n(round(pct))),
                              "obj": "%s free · warning %s, critical %s" % (gb(free), _n(o["ds_warn"]), _n(o["ds_crit"])),
                              "feed": fk, "vc": name, "ds": r["n"], "v": pct, "site": r["site"]})
        part["datastores"].extend(dsrows)
        vms = snap.get("vms") or []
        part["vms"] += len(vms)
        if o["vm_heartbeat"]:
            for v in vms:
                if v.get("power") == "poweredOn" and v.get("heartbeat") == "red":
                    conds.append({"id": "vmhung:%s:%s" % (name, v.get("name")), "kind": "vmhung", "t": "VM %s no heartbeat" % v.get("name"),
                                  "obj": "powered on, VMware Tools heartbeat red · host %s" % (v.get("host") or "-"), "feed": fk, "vc": name,
                                  "vm": v.get("name"), "host": v.get("host") or "", "site": site_of(rx, v.get("host"))})
        # sustained high: this vCenter's series
        vm_host = dict((v.get("name"), v.get("host") or "") for v in vms)
        host_cluster = dict((h.get("name"), h.get("cluster") or "") for h in hosts)
        for key, samples in hist.items():
            if not (isinstance(key, (tuple, list)) and len(key) == 4 and key[0] == src["key"]):
                continue
            _k, kind, oname, metric = key
            samples = [(float(t), v) for t, v in samples]
            v_now, start = run_of(samples, thr, sec)
            if start is None or not samples or (src.get("at") and samples[-1][0] < float(src["at"]) - 1):
                continue                      # not high now, or not in the newest good read (a VM gone)
            long_s = held(start, samples[-1][0], sec)
            label = "VM" if kind == "vm" else "Host"
            m = "CPU" if metric == "cpu" else "Memory"
            host = vm_host.get(oname, "") if kind == "vm" else oname
            if long_s >= win:
                cid = "%s:%s:%s:%s" % (metric, kind, name, oname)
                conds.append({"id": cid, "kind": metric, "t": "%s %s %s %s %%" % (label, oname, m.lower() if kind == "host" else m, _n(round(v_now))),
                              "obj": ("host %s" % host if kind == "vm" else "cluster %s" % (host_cluster.get(oname) or "-")) + " · sustained, not a blip",
                              "start": start, "feed": fk, "vc": name, "v": round(v_now, 1), "site": site_of(rx, host_cluster.get(host), host, oname),
                              "obj_kind": kind, "obj_name": oname, "metric": metric, "host": host})
                part["hot"].append({"cond": cid, "n": oname, "k": label, "m": m, "v": round(v_now, 1), "spark": spark(samples, now),
                                    "host": host, "vc": name, "feed": fk})
            else:
                part["building"].append({"n": oname, "k": label, "m": m, "v": round(v_now, 1), "min": int(long_s // 60), "vc": name})
    part["hot"].sort(key=lambda x: -x["v"])
    part["building"].sort(key=lambda x: -x["v"])
    part["datastores"].sort(key=lambda d: (-(d["v"] or 0), d["n"]))
    return part, conds, feeds


def gb(b):
    b = float(b or 0)
    return ("%.1f TB" % (b / 1024 ** 4)) if b >= 1024 ** 4 else ("%.0f GB" % (b / 1024 ** 3))


def storage_part(nm, nm_asof, vm, o, now, why="", view=None):
    """the NetApp page's newest run (netapp_live's model) + the vCenters' datastores -> (part, conds, feeds)"""
    conds, feeds = [], {}
    part = {"configured": bool(nm), "why": why, "clusters": [], "nodes": [0, 0], "volumes": 0, "volOffline": None, "smRel": 0,
            "aggr": [], "ds": [], "dsTotal": 0, "smBroken": [], "paths": {}, "source": "report database"}
    rx = site_rule(o["site_pattern"])
    if nm:
        run = nm.get("run") or {}
        feeds["ontap"] = {"name": "NetApp - refresh volume list (report database)", "asof": nm_asof, "state": "ok" if nm_asof else "never",
                          "why": "", "src": "the refresh job's newest run in the report database (job %s)" % (run.get("job") or "-")}
        part["clusters"] = sorted(set(str(x.get("c") or "") for x in nm.get("aggrs") or [] if x.get("c")))
        for a in nm.get("aggrs") or []:
            pct = float(a.get("pct") or 0)
            r = {"n": a.get("a") or "", "node": a.get("node") or "", "c": a.get("c") or "", "v": pct, "size": int(a.get("size") or 0),
                 "free": int(a.get("free") or 0), "feed": "ontap", "site": a.get("site") or site_of(rx, a.get("c"))}
            if pct >= o["aggr_warn"]:
                k = "aggrfull" if pct >= o["aggr_crit"] else "aggrwarn"
                cid = "aggr:%s:%s" % (r["c"], r["n"])
                r["cond"] = cid
                conds.append({"id": cid, "kind": k, "t": "%s %s %% used" % (r["n"], _n(round(pct))), "obj": "%s free · cluster %s · warning %s, critical %s"
                              % (gb(r["free"]), r["c"], _n(o["aggr_warn"]), _n(o["aggr_crit"])), "feed": "ontap", "aggr": r["n"], "cluster": r["c"],
                              "v": pct, "site": r["site"]})
            part["aggr"].append(r)
        part["aggr"].sort(key=lambda a: -a["v"])
        parts = nm.get("parts") or {}
        sm = parts.get("snapmirror") or {}
        part["smRel"] = int((sm.get("counts") or {}).get("all") or 0)
        for r in sm.get("rows") or []:
            if r.get("st") == "critical":
                cid = "sm:" + str(r.get("dst"))
                part["smBroken"].append({"dst": r.get("dst") or "", "src": r.get("src") or "", "why": r.get("why") or r.get("word") or "",
                                         "word": r.get("word") or "", "cond": cid, "c": r.get("dc") or ""})
                conds.append({"id": cid, "kind": "sm", "t": "SnapMirror %s %s" % (r.get("dst"), (r.get("word") or "broken").lower()),
                              "obj": "from %s · %s" % (r.get("src") or "-", r.get("why") or r.get("state") or ""), "feed": "ontap", "cluster": r.get("dc") or "",
                              "site": r.get("site") or ""})
        nodes = (parts.get("nodes") or {}).get("rows") or []
        part["nodes"] = [sum(1 for n in nodes if n.get("state") == "up"), len(nodes)]
        for n in nodes:
            if n.get("state") != "up":
                conds.append({"id": "node:%s:%s" % (n.get("cluster"), n.get("node")), "kind": "node", "t": "Storage node %s %s" % (n.get("node"), n.get("state") or "down"),
                              "obj": "cluster %s · takeover %s" % (n.get("cluster"), n.get("takeover") or "?"), "feed": "ontap", "cluster": n.get("cluster") or "",
                              "site": n.get("site") or ""})
        for c in (parts.get("clusters") or {}).get("rows") or []:
            if str(c.get("why") or "").startswith("not read"):
                conds.append({"id": "node:%s" % c.get("name"), "kind": "node", "t": "Storage cluster %s not answering" % c.get("name"),
                              "obj": c.get("why") or "", "feed": "ontap", "cluster": c.get("name") or "", "site": c.get("site") or ""})
        part["volumes"] = len(nm.get("volumes") or [])
        for v in nm.get("volumes") or []:
            if not v.get("ds"):
                continue
            p = part["paths"].setdefault(v["ds"], {"rows": []})
            p["rows"].append({"cluster": v.get("c") or "", "svm": v.get("svm") or "", "vol": v.get("v") or "", "lun": v.get("lun") or "",
                              "kind": v.get("kind") or "", "size": v.get("size"), "used": v.get("used"), "pct": v.get("pct"), "src": v.get("src") or "",
                              "vol_size": v.get("vol_size"), "aggr": v.get("aggr") or "", "aggr_free": v.get("aggr_free"), "thick": bool(v.get("thick")),
                              "st": v.get("st") or "ok"})
    live = ontap_live(view, o, rx, part, conds, feeds)
    if live and not nm:
        part["configured"] = True
    # the datastores: the vCenters' (live) - else the NetApp run's datastore rows
    if vm.get("datastores"):
        part["ds"] = [dict(d) for d in vm["datastores"]]
    elif nm:
        seen = {}
        for v in nm.get("volumes") or []:
            if v.get("ds") and v["ds"] not in seen and v.get("src") == "datastore used":
                seen[v["ds"]] = {"n": v["ds"], "v": v.get("pct"), "free": v.get("free"), "size": v.get("size"), "feed": "ontap", "type": v.get("kind") or "",
                                 "site": v.get("site") or ""}
        part["ds"] = sorted(seen.values(), key=lambda d: -(d["v"] or 0))
        for d in part["ds"]:
            if d["v"] is not None and d["v"] >= o["ds_warn"]:
                conds.append({"id": "ds::%s" % d["n"], "kind": "dsfull" if d["v"] >= o["ds_crit"] else "dswarn", "t": "%s %s %% full" % (d["n"], _n(round(d["v"]))),
                              "obj": "%s free · warning %s, critical %s" % (gb(d["free"]), _n(o["ds_warn"]), _n(o["ds_crit"])), "feed": "ontap", "ds": d["n"],
                              "v": d["v"], "site": d["site"]})
    part["dsTotal"] = len(part["ds"])
    return part, conds, feeds


def ontap_live(view, o, rx, part, conds, feeds):
    """the live ONTAP reads (wall_live.OntapSource, when the portal has the "NetApp ONTAP" login): they take the place of the
    NetApp run's aggregates, nodes and SnapMirror for their clusters, and add volumes / LUNs offline. -> True when there is one"""
    srcs = [dict(s, key=k) for k, s in ((view or {}).get("sources") or {}).items() if s.get("kind") == "ontap"]
    if not srcs:
        return False
    live_cl = set()
    if any(x.get("snap") for x in srcs):
        part["nodes"] = [0, 0]                  # the live reads count the nodes (the NetApp run's count would double them)
    for src in sorted(srcs, key=lambda x: str(x.get("name"))):
        fk, snap, state = src["key"], src.get("snap") or {}, src.get("state") or "never"
        cl = str(snap.get("cluster") or src.get("name") or "")
        feeds[fk] = {"name": "ONTAP " + cl, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % o["seconds"], "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        if state in ("auth", "error"):
            conds.append({"id": "node:%s" % cl, "kind": "node", "t": "Storage cluster %s %s" % (cl, "login failed" if state == "auth" else "not answering"),
                          "obj": "login failed - check the credential" if state == "auth" else (src.get("why") or ""), "feed": "", "cluster": cl,
                          "start": src.get("fail_since"), "site": site_of(rx, cl)})
        if not snap:
            continue
        live_cl.add(cl)
        part["aggr"] = [a for a in part["aggr"] if a["c"] != cl]
        conds[:] = [c for c in conds if not (c.get("feed") == "ontap" and c.get("cluster") == cl)]
        part["smBroken"] = [b for b in part["smBroken"] if b.get("c") != cl]
        for a in snap.get("aggrs") or []:
            size_b, used = int(a.get("size") or 0), int(a.get("used") or 0)
            pct = round(100.0 * used / size_b, 1) if size_b else 0.0
            r = {"n": a.get("name") or "", "node": a.get("node") or "", "c": cl, "v": pct, "size": size_b, "free": max(0, size_b - used), "feed": fk, "site": site_of(rx, cl)}
            if pct >= o["aggr_warn"]:
                cid = "aggr:%s:%s" % (cl, r["n"])
                r["cond"] = cid
                conds.append({"id": cid, "kind": "aggrfull" if pct >= o["aggr_crit"] else "aggrwarn", "t": "%s %s %% used" % (r["n"], _n(round(pct))),
                              "obj": "%s free · cluster %s · warning %s, critical %s" % (gb(r["free"]), cl, _n(o["aggr_warn"]), _n(o["aggr_crit"])),
                              "feed": fk, "aggr": r["n"], "cluster": cl, "v": pct, "site": r["site"]})
            part["aggr"].append(r)
        nodes = snap.get("nodes") or []
        for n in nodes:
            if str(n.get("state") or "") not in ("up", ""):
                conds.append({"id": "node:%s:%s" % (cl, n.get("name")), "kind": "node", "t": "Storage node %s %s" % (n.get("name"), n.get("state")),
                              "obj": "cluster %s · takeover %s" % (cl, {True: "possible", False: "not possible"}.get(n.get("takeover"), "?")), "feed": fk,
                              "cluster": cl, "site": site_of(rx, cl)})
        part["nodes"] = [part["nodes"][0] + sum(1 for n in nodes if n.get("state") == "up"), part["nodes"][1] + len(nodes)]
        off = 0
        for v in snap.get("volumes") or []:
            if str(v.get("state") or "online") != "online":
                off += 1
                conds.append({"id": "voloff:%s:%s:%s" % (cl, v.get("svm"), v.get("name")), "kind": "voloff", "t": "Volume %s %s" % (v.get("name"), v.get("state")),
                              "obj": "SVM %s · cluster %s" % (v.get("svm"), cl), "feed": fk, "cluster": cl, "site": site_of(rx, cl)})
        for x in snap.get("luns") or []:
            if str(x.get("state") or "online") != "online":
                off += 1
                conds.append({"id": "voloff:%s:%s" % (cl, x.get("path")), "kind": "voloff", "t": "LUN %s %s" % (x.get("path"), x.get("state")),
                              "obj": "SVM %s · volume %s" % (x.get("svm"), x.get("volume")), "feed": fk, "cluster": cl, "site": site_of(rx, cl)})
        part["volOffline"] = (part["volOffline"] or 0) + off
        sm = snap.get("snapmirror") or []
        part["smRel"] += len(sm)
        for r in sm:
            if r.get("healthy") is False or str(r.get("state") or "").replace("-", "_") == "broken_off":
                cid = "sm:" + str(r.get("dst"))
                word = "broken off" if str(r.get("state") or "").replace("-", "_") == "broken_off" else "unhealthy"
                part["smBroken"].append({"dst": r.get("dst") or "", "src": r.get("src") or "", "why": r.get("why") or "", "word": word.capitalize(), "cond": cid, "c": cl})
                conds.append({"id": cid, "kind": "sm", "t": "SnapMirror %s %s" % (r.get("dst"), word), "obj": "from %s · %s" % (r.get("src") or "-", r.get("why") or r.get("state") or ""),
                              "feed": fk, "cluster": cl, "site": site_of(rx, cl)})
    part["aggr"].sort(key=lambda a: -a["v"])
    part["clusters"] = sorted(set(part["clusters"]) | live_cl)
    part["source"] = "live" if live_cl else part["source"]
    return True


def build(view, nm, nm_asof, opts, tracker, now, enclave="", title="", nm_why=""):
    """-> the answer of /api/wall (times: epoch seconds)"""
    o = opts_of(opts)
    ks = kinds(o)
    vm, c1, f1 = vmware_part(view, o, now)
    st, c2, f2 = storage_part(nm, nm_asof, vm, o, now, nm_why, view)
    for c in c1 + c2:
        k = ks[c["kind"]]
        c.update(area=k["area"], sev=k["sev"], end=None)
    conds = tracker.update(c1 + c2, now) if tracker is not None else [dict(c, start=c.get("start") or now) for c in c1 + c2]
    # a datastore / aggregate row points at its condition
    by = {}
    for c in conds:
        if c.get("end") is None and c.get("ds"):
            by[("ds", c.get("vc", ""), c["ds"])] = c["id"]
    for d in st["ds"]:
        cid = by.get(("ds", d.get("vc", ""), d["n"]))
        if cid:
            d["cond"] = cid
    feeds = dict(f1)
    feeds.update(f2)
    for f in feeds.values():
        f["age"] = None if not f.get("asof") else max(0, int(now - float(f["asof"])))
        f["fresh"] = fstate(f["age"], o)
    area_feeds = {"vmware": sorted(f1), "storage": sorted(f2) + sorted(f1)}
    areas = []
    for a in AREAS:
        x = dict(a)
        x["on"] = a["id"] in BUILT and (bool(area_feeds.get(a["id"])))
        x["built"] = a["id"] in BUILT
        x["feeds"] = area_feeds.get(a["id"], [])
        x["not_on_wall"] = NOT_ON_WALL[a["id"]]
        if a["id"] == "storage" and not st["configured"] and not vm["configured"]:
            x["why"] = st["why"] or "no NetApp run in the report database and no vCenter read"
        elif a["id"] == "vmware" and not vm["configured"]:
            x["why"] = "no vCenter login: the portal's deploy needs the \"VMware vCenter\" credential (portal_wall_vcenter)"
        areas.append(x)
    sites = sorted(set(c.get("site") for c in conds if c.get("site")) | set(h["site"] for h in vm["hosts"] if h.get("site")))
    return {"now": now, "title": title or "Ops center", "enclave": enclave, "late": o["late"], "stale": o["stale"], "seconds": o["seconds"],
            "areas": areas, "feeds": feeds, "kinds": ks, "conds": conds, "vmware": vm, "storage": st,
            "thresholds": {"ds": [o["ds_warn"], o["ds_crit"]], "aggr": [o["aggr_warn"], o["aggr_crit"]], "high": [o["high_pct"], o["high_minutes"]],
                           "maintenance_expected": o["maintenance_expected"], "vm_heartbeat": o["vm_heartbeat"]},
            "sites": [{"id": s, "place": o["site_places"].get(s)} for s in sites]}
