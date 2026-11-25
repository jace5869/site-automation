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
# GREEN ONLY FROM AN ALLOW-LIST (Codex verify 4): each object is OK only when its fields say exactly so; a known bad value raises
# its condition; anything else (missing, None, "gray", an unexpected word) is UNKNOWN - the area not whole, grey, never OK, and
# never what clears a condition
POWER_KNOWN = ("poweredOn", "poweredOff", "suspended")
SM_GOOD = ("snapmirrored", "in_sync")
STATE_BAD = ("offline", "restricted", "error", "mixed", "nvfail", "space_error", "foreign_lun_error", "aggregate_offline", "volume_offline")
NODE_BAD = ("down", "taken_over", "waiting_for_giveback", "degraded")

AREAS = (
    {"id": "storage", "name": "Storage", "sub": "NetApp", "team": "Storage team"},
    {"id": "vmware", "name": "VMware", "sub": "vCenter", "team": "VMware team"},
    {"id": "vdi", "name": "VDI", "sub": "Horizon", "team": "VDI team"},
    {"id": "ad", "name": "Active Directory", "short": "AD", "sub": "", "team": "AD team"},
    {"id": "tickets", "name": "Outages & problems", "short": "Outages & PRB", "sub": "ServiceNow", "team": "Service desk"},
)
BUILT = ("storage", "vmware")    # stage 1: the others are grey tiles "not on the wall yet"
STORAGE_OFF = "not on the wall yet: no live ONTAP cluster (portal_wall_ontap_clusters) - the wall's Storage is read live only"
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
    """Conditions by their id: the start stays while one is open (the earliest seen). A condition clears ONLY when the same
    object is seen OK (its id in `ok`: the object in a fresh, whole read, passing the allow-list) - kept as cleared for
    KEEP_CLEARED_S. One that is no longer in the read but was not seen OK stays open, marked last_known (Codex verify 4: loss
    of data is never a recovery). Memory only: a restart of the portal starts again."""

    def __init__(self, keep=KEEP_CLEARED_S):
        self.keep, self.open, self.cleared = keep, {}, collections.OrderedDict()
        self.first = None                      # the first read it saw: "watching since" (memory only)

    def update(self, conds, now, ok=()):
        ok = set(ok or ())
        if self.first is None:
            self.first = now
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
        kept = []
        for cid in [k for k in self.open if k not in seen]:
            if cid not in ok:
                kept.append(dict(self.open[cid], last_known=True))     # not seen OK: open, as last known
                continue
            c = self.open.pop(cid)
            c["end"] = now
            self.cleared[cid] = c
        for cid in [k for k, c in self.cleared.items() if now - c["end"] > self.keep]:
            self.cleared.pop(cid)
        return conds + kept + [dict(c) for c in self.cleared.values()]


# ---- the model -----------------------------------------------------------------------------------------------------------------
def _vc_feed(src):
    return "vc:" + str(src.get("name") or "")


def vmware_part(view, o, now):
    """the vCenters' last good reads -> (part, conds, feeds, ok): ok = [(feed, condition id)] of the objects seen OK"""
    conds, feeds, ok = [], {}, []
    part = {"configured": False, "vcenters": [], "clusters": [], "hosts": [], "datastores": [], "hot": [], "building": [], "vms": 0,
            "incomplete": []}                       # what this read lacks: the area is never green while it lacks anything
    srcs = [dict(s, key=k) for k, s in ((view or {}).get("sources") or {}).items() if s.get("kind") == "vcenter"]
    hist = (view or {}).get("history") or {}
    if not srcs:
        return part, conds, feeds, ok
    part["configured"] = True
    rx = site_rule(o["site_pattern"])
    thr, win, sec = o["high_pct"], o["high_minutes"] * 60, o["seconds"]
    for src in sorted(srcs, key=lambda s: str(s.get("name"))):
        name, fk = str(src.get("name") or ""), _vc_feed(src)
        state = src.get("state") or "never"
        feeds[fk] = {"name": "vCenter " + name, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % sec, "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        part["vcenters"].append({"name": name, "up": state == "ok", "state": state, "why": src.get("why") or "", "feed": fk})
        if state == "ok":
            ok.append((fk, "vc:" + name))
        if state in ("auth", "error"):
            why = (src.get("why") or "login failed - check the credential") if state == "auth" else (src.get("why") or "not answering")
            conds.append({"id": "vc:" + name, "kind": "vc", "t": "vCenter %s %s" % (name, "login failed" if state == "auth" else "not answering"),
                          "obj": why, "start": src.get("fail_since") or now, "feed": "", "vc": name, "site": site_of(rx, name)})
        snap = src.get("snap") or {}
        if not src.get("snap"):
            part["incomplete"].append("vCenter %s: no inventory read yet" % name)
        hosts = snap.get("hosts") or []
        by_cluster = collections.OrderedDict()
        for h in hosts:
            by_cluster.setdefault(h.get("cluster") or "(standalone)", []).append(h)
        for cn, hs in by_cluster.items():
            up = [h for h in hs if h.get("state") == "connected"]          # Codex 1.130.0: only a host vCenter says is connected is up
            cpus = [h["cpu_pct"] for h in up if h.get("cpu_pct") is not None]
            mems = [h["mem_pct"] for h in up if h.get("mem_pct") is not None]
            part["clusters"].append({"n": cn, "vc": name, "feed": fk, "up": len(up), "of": len(hs),
                                     "cpu": round(sum(cpus) / len(cpus), 1) if cpus else None,
                                     "mem": round(sum(mems) / len(mems), 1) if mems else None,
                                     "down": [h["name"] for h in hs if h.get("state") in DOWN_STATES],
                                     "unknown": [h["name"] for h in hs if h.get("state") not in DOWN_STATES + ("connected",)],
                                     "maint": [h["name"] for h in hs if h.get("maint") is True], "site": site_of(rx, cn, hs[0].get("name"))})
        for h in hosts:
            hn, st = h.get("name") or "", h.get("state") or ""
            down = st in DOWN_STATES
            maint = h.get("maint") if isinstance(h.get("maint"), bool) else None
            row = {"n": hn, "vc": name, "cluster": h.get("cluster") or "", "state": st, "maint": maint, "feed": fk,
                   "cpu": h.get("cpu_pct"), "mem": h.get("mem_pct"), "vms": sorted(h.get("vms") or [])[:200], "nvms": len(h.get("vms") or []),
                   "ds": sorted(h.get("datastores") or []), "power": h.get("power") or "", "site": site_of(rx, h.get("cluster"), hn), "st": "ok"}
            if down:
                row["st"] = "crit"
                conds.append({"id": "host:%s:%s" % (name, hn), "kind": "host", "t": "%s %s" % (hn, "not responding" if st == "notResponding" else "disconnected"),
                              "obj": "cluster %s · %d VMs on it" % (row["cluster"] or "-", row["nvms"]), "feed": fk, "vc": name, "host": hn,
                              "cluster": row["cluster"], "site": row["site"]})
            elif st != "connected" or maint is None:
                row["st"] = "stale"                    # its state or its maintenance mode not read: unknown - never up, never green
            else:
                ok.append((fk, "host:%s:%s" % (name, hn)))
                if maint and not any(fnmatch.fnmatch(hn.lower(), p.lower()) for p in o["maintenance_expected"]):
                    row["st"] = "warn"
                    conds.append({"id": "maint:%s:%s" % (name, hn), "kind": "maint", "t": "%s in maintenance mode" % hn,
                                  "obj": "cluster %s · not listed in portal_wall_maintenance_expected" % (row["cluster"] or "-"), "feed": fk, "vc": name,
                                  "host": hn, "cluster": row["cluster"], "site": row["site"]})
                else:
                    ok.append((fk, "maint:%s:%s" % (name, hn)))
                for metric in ("cpu", "mem"):
                    v_ = row[metric]
                    if v_ is not None and v_ < thr:
                        ok.append((fk, "%s:host:%s:%s" % (metric, name, hn)))
            if maint is None and st == "connected":
                part["incomplete"].append("host %s: maintenance mode not read" % hn)
            if st not in DOWN_STATES + ("connected",):
                part["incomplete"].append("host %s: state not read" % hn)
            elif st == "connected" and (row["cpu"] is None or row["mem"] is None):
                part["incomplete"].append("host %s: CPU or memory not read" % hn)
            part["hosts"].append(row)
        dsrows = []
        for d in snap.get("datastores") or []:
            cap, free = int(d.get("capacity") or 0), d.get("free")
            # not read, or impossible (free below 0 or over the capacity): unknown - never 100 %, never OK (Codex r5 #14)
            pct = round(100.0 * (cap - int(free)) / cap, 1) if cap and free is not None and 0 <= int(free) <= cap else None
            free = int(free or 0)
            if pct is None:
                part["incomplete"].append("datastore %s: capacity or free space not read" % (d.get("name") or "?"))
            acc = d.get("accessible") if isinstance(d.get("accessible"), bool) else None
            r = {"n": d.get("name") or "", "vc": name, "feed": fk, "type": d.get("type") or "", "size": cap, "free": free, "v": pct,
                 "accessible": acc, "hosts": sorted(d.get("hosts") or []), "nvms": int(d.get("vms") or 0)}
            r["site"] = site_of(rx, r["n"], (r["hosts"] or [""])[0])
            dsrows.append(r)
            if acc is None:
                part["incomplete"].append("datastore %s: accessibility not read" % r["n"])
            elif acc:
                ok.append((fk, "dsi:%s:%s" % (name, r["n"])))
                if pct is not None and pct < o["ds_warn"]:
                    ok.append((fk, "ds:%s:%s" % (name, r["n"])))
            if acc is False:
                conds.append({"id": "dsi:%s:%s" % (name, r["n"]), "kind": "dsi", "t": "Datastore %s inaccessible" % r["n"],
                              "obj": "mounted on %d hosts · %d VMs" % (len(r["hosts"]), r["nvms"]), "feed": fk, "vc": name, "ds": r["n"], "site": r["site"]})
            elif acc and pct is not None and pct >= o["ds_warn"]:
                k = "dsfull" if pct >= o["ds_crit"] else "dswarn"
                conds.append({"id": "ds:%s:%s" % (name, r["n"]), "kind": k, "t": "%s %s %% full" % (r["n"], _n(round(pct))),
                              "obj": "%s free · warning %s, critical %s" % (gb(free), _n(o["ds_warn"]), _n(o["ds_crit"])),
                              "feed": fk, "vc": name, "ds": r["n"], "v": pct, "site": r["site"]})
        part["datastores"].extend(dsrows)
        vms = snap.get("vms") or []
        part["vms"] += len(vms)
        for v in vms:
            vn, power = v.get("name"), v.get("power")
            if power not in POWER_KNOWN:
                part["incomplete"].append("VM %s: power state not read" % vn)
                continue
            if power != "poweredOn":                   # off or suspended, known: none of its conditions holds
                ok.extend((fk, x % (name, vn)) for x in ("vmhung:%s:%s", "cpu:vm:%s:%s", "mem:vm:%s:%s"))
                continue
            if v.get("cpu_pct") is None or v.get("mem_pct") is None:
                part["incomplete"].append("VM %s: CPU or memory not read" % vn)
            for metric in ("cpu", "mem"):
                if v.get(metric + "_pct") is not None and v[metric + "_pct"] < thr:
                    ok.append((fk, "%s:vm:%s:%s" % (metric, name, vn)))
            if o["vm_heartbeat"]:
                hb = v.get("heartbeat")
                if hb == "green":
                    ok.append((fk, "vmhung:%s:%s" % (name, vn)))
                elif hb != "red":                      # gray, yellow, not read: unknown - never a recovery
                    part["incomplete"].append("VM %s: heartbeat %s" % (vn, "not read" if not hb else "%s (not green or red)" % hb))
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
    return part, conds, feeds, ok


def gb(b):
    b = float(b or 0)
    return ("%.1f TB" % (b / 1024 ** 4)) if b >= 1024 ** 4 else ("%.0f GB" % (b / 1024 ** 3))


def storage_part(nm, nm_asof, vm, o, now, why="", view=None):
    """Storage - LIVE ONTAP ONLY (Codex verify 4, cut 1): the lamps, counters, SnapMirror and node checks, aggregates and Map
    dots come only from the portal's own reads of portal_wall_ontap_clusters; with none configured, Storage is not on the
    wall. The NetApp run in the report database (15-30 min old by design) is never a light: it gives only the datastore ->
    volume / LUN path card its rows, as information ("from the NetApp refresh, N min old"). -> (part, conds, feeds, ok)"""
    conds, feeds, ok = [], {}, []
    rx = site_rule(o["site_pattern"])
    srcs = sorted([dict(x, key=k) for k, x in ((view or {}).get("sources") or {}).items() if x.get("kind") == "ontap"], key=lambda x: str(x.get("name")))
    part = {"configured": bool(srcs), "why": "" if srcs else STORAGE_OFF, "incomplete": [], "ran": {"sm": False, "nodes": False}, "clusters": [],
            "nodes": [0, 0], "volumes": 0, "volOffline": None, "smRel": 0, "aggr": [], "ds": [], "dsTotal": 0, "smBroken": [], "paths": {},
            "source": "live", "paths_why": why or ""}
    ran = {"sm": [], "nodes": []}
    for src in srcs:
        fk, snap, state = src["key"], src.get("snap") or {}, src.get("state") or "never"
        addr = str(src.get("name") or "")
        cl = str(snap.get("cluster") or addr)
        site = site_of(rx, cl)
        feeds[fk] = {"name": "ONTAP " + addr, "asof": src.get("at"), "state": state, "why": src.get("why") or "",
                     "src": "live, read every %d s" % o["seconds"], "fail_since": src.get("fail_since"), "next_try": src.get("next_try")}
        if state in ("auth", "error", "paused"):
            conds.append({"id": "node:" + addr, "kind": "node",
                          "t": "Storage cluster %s %s" % (addr, "login failed" if state == "auth" else "login paused" if state == "paused" else "not answering"),
                          "obj": (src.get("why") or "login failed - check the credential") if state == "auth" else (src.get("why") or ""), "feed": "", "cluster": cl,
                          "start": src.get("fail_since"), "site": site})
        elif state == "ok":
            ok.append((fk, "node:" + addr))
        if not snap:
            part["incomplete"].append("ONTAP %s: not read yet" % addr)
            ran["sm"].append(False)
            ran["nodes"].append(False)
            continue
        if cl not in part["clusters"]:
            part["clusters"].append(cl)
        for k, why_ in (snap.get("failed") or {}).items():
            part["incomplete"].append("ONTAP %s: %s not read whole (%s)" % (addr, k, why_))
        if snap.get("aggrs") is None:
            part["incomplete"].append("ONTAP %s: aggregates not read" % addr)
        for a in snap.get("aggrs") or []:
            size_b, used = a.get("size"), a.get("used")
            pct = round(100.0 * used / size_b, 1) if size_b and used is not None and 0 <= used <= size_b else None    # not read or impossible: unknown
            cid = "aggr:%s:%s" % (addr, a.get("name") or "")
            r = {"n": a.get("name") or "", "node": a.get("node") or "", "c": cl, "addr": addr, "ref": "%s|%s" % (fk, a.get("name") or ""), "v": pct,
                 "size": size_b or 0, "free": max(0, size_b - used) if pct is not None else None, "feed": fk, "site": site}
            if pct is None:
                part["incomplete"].append("aggregate %s (%s): size or use not read" % (r["n"], addr))
            elif pct >= o["aggr_warn"]:
                r["cond"] = cid
                conds.append({"id": cid, "kind": "aggrfull" if pct >= o["aggr_crit"] else "aggrwarn", "t": "%s %s %% used" % (r["n"], _n(round(pct))),
                              "obj": "%s free · cluster %s · warning %s, critical %s" % (gb(r["free"]), cl, _n(o["aggr_warn"]), _n(o["aggr_crit"])),
                              "feed": fk, "aggr": r["n"], "ref": r["ref"], "cluster": cl, "v": pct, "site": site})
            else:
                ok.append((fk, cid))
            part["aggr"].append(r)
        nodes = snap.get("nodes")
        known = True
        for n in nodes or []:
            ns, cid = n.get("state"), "node:%s:%s" % (addr, n.get("name"))
            if ns == "up":
                ok.append((fk, cid))
            elif ns in NODE_BAD:
                conds.append({"id": cid, "kind": "node", "t": "Storage node %s %s" % (n.get("name"), ns),
                              "obj": "cluster %s · takeover %s" % (cl, {True: "possible", False: "not possible"}.get(n.get("takeover"), "?")), "feed": fk,
                              "cluster": cl, "site": site})
            else:
                known = False
                part["incomplete"].append("node %s: state %s" % (n.get("name"), "not read" if ns is None else "%r not known" % (ns,)))
        ran["nodes"].append(nodes is not None and known)
        part["nodes"] = [part["nodes"][0] + sum(1 for n in nodes or [] if n.get("state") == "up"), part["nodes"][1] + len(nodes or [])]
        off = 0
        for coll, label in (("volumes", "Volume"), ("luns", "LUN")):
            if snap.get(coll) is None:
                part["incomplete"].append("ONTAP %s: %s not read" % (addr, coll))
            for v in snap.get(coll) or []:
                nm_ = v.get("name") if coll == "volumes" else v.get("path")
                vs, cid = v.get("state"), "voloff:%s:%s:%s" % (addr, v.get("svm"), nm_)
                if vs == "online":
                    ok.append((fk, cid))
                elif vs in STATE_BAD:
                    off += 1
                    conds.append({"id": cid, "kind": "voloff", "t": "%s %s %s" % (label, nm_, vs),
                                  "obj": "SVM %s · %s" % (v.get("svm"), "cluster " + cl if coll == "volumes" else "volume %s" % v.get("volume")), "feed": fk,
                                  "cluster": cl, "site": site})
                else:
                    part["incomplete"].append("%s %s: state %s" % (label.lower(), nm_, "not read" if vs is None else "%r not known" % (vs,)))
        part["volumes"] += len(snap.get("volumes") or [])
        if snap.get("volumes") is not None or snap.get("luns") is not None:
            part["volOffline"] = (part["volOffline"] or 0) + off
        sm = snap.get("snapmirror")
        known = True
        part["smRel"] += len(sm or [])
        for r in sm or []:
            st_ = str(r.get("state") or "").replace("-", "_")
            cid = "sm:%s:%s" % (addr, r.get("dst"))
            if r.get("healthy") is False or st_ == "broken_off":
                word = "broken off" if st_ == "broken_off" else "unhealthy"
                part["smBroken"].append({"dst": r.get("dst") or "", "src": r.get("src") or "", "why": r.get("why") or "", "word": word.capitalize(), "cond": cid, "c": cl})
                conds.append({"id": cid, "kind": "sm", "t": "SnapMirror %s %s" % (r.get("dst"), word),
                              "obj": "from %s · %s" % (r.get("src") or "-", r.get("why") or r.get("state") or ""), "feed": fk, "cluster": cl, "site": site})
            elif r.get("healthy") is True and st_ in SM_GOOD:
                ok.append((fk, cid))
            else:
                known = False
                part["incomplete"].append("SnapMirror %s: health or state not known (healthy %s, state %s)" % (r.get("dst"), r.get("healthy"), r.get("state")))
        ran["sm"].append(sm is not None and known)
    if srcs:
        # the datastores (Storage's fill gauges): the vCenters' live reads
        part["ds"] = [dict(d) for d in vm.get("datastores") or []]
        for d in part["ds"]:
            if d.get("v") is None:
                part["incomplete"].append("datastore %s: capacity or free space not read" % d["n"])
            if d.get("accessible") is None:
                part["incomplete"].append("datastore %s: accessibility not read" % d["n"])
    if nm:
        # the path card only: which volume / LUN is behind a datastore - information, never a status
        feeds["netapp-run"] = {"name": "NetApp refresh (report database)", "asof": nm_asof, "state": "ok" if nm_asof and not why else "error",
                               "why": why or "", "src": "the refresh job's newest run - information on the storage path card, never a light"}
        for v in nm.get("volumes") or []:
            if not v.get("ds"):
                continue
            part["paths"].setdefault(v["ds"], {"rows": []})["rows"].append(
                {"cluster": v.get("c") or "", "svm": v.get("svm") or "", "vol": v.get("v") or "", "lun": v.get("lun") or "", "kind": v.get("kind") or "",
                 "size": v.get("size"), "used": v.get("used"), "pct": v.get("pct"), "src": v.get("src") or "", "vol_size": v.get("vol_size"),
                 "aggr": v.get("aggr") or "", "aggr_free": v.get("aggr_free"), "thick": bool(v.get("thick")), "feed": "netapp-run", "info": True})
    part["aggr"].sort(key=lambda a: -(a["v"] if a["v"] is not None else 101))
    part["ran"] = dict((k, bool(v) and all(v)) for k, v in ran.items())
    part["clusters"].sort()
    part["dsTotal"] = len(part["ds"])
    return part, conds, feeds, ok


def build(view, nm, nm_asof, opts, tracker, now, enclave="", title="", nm_why=""):
    """-> the answer of /api/wall (times: epoch seconds)"""
    o = opts_of(opts)
    ks = kinds(o)
    vm, c1, f1, ok1 = vmware_part(view, o, now)
    st, c2, f2, ok2 = storage_part(nm, nm_asof, vm, o, now, nm_why, view)
    if not st["configured"]:                   # Storage is not on the wall: none of its conditions (a datastore's fill) either
        c1 = [c for c in c1 if ks[c["kind"]]["area"] != "storage"]
    fall = dict(f1)
    fall.update(f2)
    for c in c1 + c2:
        k = ks[c["kind"]]
        c.update(area=k["area"], sev=k["sev"], end=None)
        c["seen"] = (fall.get(c.get("feed")) or {}).get("asof") or c.get("start")     # when its data was read (a last-known one: last seen)
    # what clears a condition: its object seen OK in a fresh read (its feed's latest read worked, within the stale limit)
    cur = dict((k, f.get("state") == "ok" and f.get("asof") is not None and now - float(f["asof"]) <= o["stale"]) for k, f in fall.items())
    ok_ids = set(cid for fk, cid in ok1 + ok2 if cur.get(fk))
    conds = tracker.update(c1 + c2, now, ok_ids) if tracker is not None else [dict(c, start=c.get("start") or now) for c in c1 + c2]
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
        # the state at this answer (the page works the age out itself every second - no age here: the ETag stays until a change)
        f["fresh"] = fstate(None if not f.get("asof") else max(0, int(now - float(f["asof"]))), o)
    area_feeds = {"vmware": sorted(f1), "storage": (sorted(k for k in f2 if k != "netapp-run") + sorted(f1)) if st["configured"] else []}
    areas = []
    for a in AREAS:
        x = dict(a)
        x["on"] = a["id"] in BUILT and (st["configured"] if a["id"] == "storage" else bool(area_feeds.get(a["id"])))
        x["built"] = a["id"] in BUILT
        x["feeds"] = area_feeds.get(a["id"], [])
        x["not_on_wall"] = NOT_ON_WALL[a["id"]]
        inc = (vm["incomplete"] if a["id"] == "vmware" else st["incomplete"] if a["id"] == "storage" else [])
        x["complete"], x["incomplete"] = not inc, inc[:5] + (["... %d more" % (len(inc) - 5)] if len(inc) > 5 else [])
        if a["id"] == "storage" and not st["configured"]:
            x["why"] = STORAGE_OFF
        elif a["id"] == "vmware" and not vm["configured"]:
            x["why"] = "no vCenter login: the portal's deploy needs the \"VMware vCenter\" credential (portal_wall_vcenter)"
        areas.append(x)
    sites = sorted(set(c.get("site") for c in conds if c.get("site")) | set(h["site"] for h in vm["hosts"] if h.get("site")))
    return {"now": now, "since": tracker.first if tracker is not None else None, "title": title or "Ops center", "enclave": enclave, "late": o["late"], "stale": o["stale"], "seconds": o["seconds"],
            "areas": areas, "feeds": feeds, "kinds": ks, "conds": conds, "vmware": vm, "storage": st,
            "thresholds": {"ds": [o["ds_warn"], o["ds_crit"]], "aggr": [o["aggr_warn"], o["aggr_crit"]], "high": [o["high_pct"], o["high_minutes"]],
                           "maintenance_expected": o["maintenance_expected"], "vm_heartbeat": o["vm_heartbeat"]},
            "sites": [{"id": s, "place": o["site_places"].get(s)} for s in sites]}
