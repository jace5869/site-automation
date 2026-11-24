# -*- coding: utf-8 -*-
"""The portal's NetApp page (/portal/netapp): what NetApp - refresh volume list keeps in the report database
(roles/ontap_report/tasks/live_db.yml). ontap_live_payload makes one run's rows - every volume the list read (the ones the
expand job offers with their survey choice, the others with their reason), every aggregate, and, when the health parts were
read (ontap_live_health, ontap_live_perf), SnapMirror, the nodes, the disks, the error events and the busiest volumes and
LUNs - each part with what could not be read. GET only; the growth and the days come later, from the database's points
(site_ontap_live_db)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import importlib.util
import os
import re
import sys

try:
    from ansible.module_utils import site_ontap_expand as X
except ImportError:                     # the unit tests
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils"))
    import site_ontap_expand as X

# the health report's own readers of ONTAP's answers (times, lags, LDAP), loaded by its path (a filter is not a package)
_spec = importlib.util.spec_from_file_location("ontap_filters_for_live", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ontap_filters.py"))
OF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(OF)

ROWS_MAX = 50                           # rows of a health part kept (problems first); the counts are of all
SEV = {"emergency": ("critical", "Emergency"), "alert": ("critical", "Alert"), "error": ("warning", "Error")}


def _recs(c, key):
    return ((c.get("data") or {}).get(key) or {}).get("records") or []


def _err(c, keys):
    """what of keys could not be read on this cluster (one line each)"""
    out = []
    for k in keys:
        d = (c.get("data") or {}).get(k) or {}
        if d.get("error"):
            out.append("%s: %s not read - %s" % (c["_name"], k, str(d["error"])[:200]))
        elif d.get("truncated"):
            out.append("%s: %s - only the first %d records were read" % (c["_name"], k, len(d.get("records") or [])))
    return out


def _key(e):
    return (str(e.get("address") or "").lower(), e.get("svm") or "", e.get("volume") or "", e.get("lun") or "")


def _iso(t):
    return t.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def period_seconds(s):
    """A cluster schedule ({name, type, cron, interval}) -> about how often it runs (seconds), or None."""
    if not isinstance(s, dict):
        return None
    if s.get("interval"):
        return OF.duration_seconds(s["interval"])
    cron = s.get("cron") or {}
    if not isinstance(cron, dict):
        return None
    mins, hours = cron.get("minutes") or [], cron.get("hours") or []
    days, wdays, months = cron.get("days") or [], cron.get("weekdays") or [], cron.get("months") or []
    if months:
        return 365 * 86400 // max(1, len(months))
    if days:
        return 30 * 86400 // max(1, len(days))
    if wdays:
        return 7 * 86400 // max(1, len(wdays))
    if hours:
        return 86400 // max(1, len(hours))
    return 3600 // max(1, len(mins)) if mins else 60


def _snapmirror(up, o):
    lw, lc = float(o.get("lag_warn_hours", 24)) * 3600, float(o.get("lag_crit_hours", 48)) * 3600
    rows, errs, n = [], [], 0
    for c in up:
        cli = _recs(c, "snapmirror_cli")
        recs = cli or _recs(c, "snapmirror")
        errs += _err(c, ["snapmirror_cli"] if cli else ["snapmirror_cli", "snapmirror"])
        scheds = dict((s.get("name"), period_seconds(s)) for s in _recs(c, "schedules"))
        for r in recs:
            n += 1
            if cli:
                src, dst, state, status = r.get("source_path"), r.get("destination_path"), r.get("state", ""), r.get("status", "")
                healthy, lag = OF.as_bool(r.get("healthy")), OF.duration_seconds(r.get("lag_time"))
                last, why, sched = r.get("last_transfer_end_timestamp"), r.get("unhealthy_reason") or r.get("last_transfer_error") or "", r.get("schedule") or ""
                prog = r.get("total_progress")
            else:
                src, dst, state = OF.g(r, "source.path"), OF.g(r, "destination.path"), r.get("state", "")
                status = OF.g(r, "transfer.state", "") or ""
                healthy, lag = OF.as_bool(r.get("healthy")), OF.duration_seconds(r.get("lag_time"))
                last, prog, sched = OF.g(r, "transfer.end_time"), OF.g(r, "transfer.bytes_transferred"), ""
                why = "; ".join(x.get("message", "") for x in (r.get("unhealthy_reason") or []) if isinstance(x, dict))
            st_l, status_l = str(state).lower().replace("-", "_"), str(status).lower()
            per = scheds.get(sched) if sched else None
            transferring = "transferring" in status_l
            if healthy is False or st_l == "broken_off" or (lag is not None and lag >= lc):
                st, word = "critical", "Broken off" if st_l == "broken_off" else ("Unhealthy" if healthy is False else "Lagging")
            elif (lag is not None and (lag >= lw or (per and lag > 2 * per))) or st_l in ("paused", "quiesced", "uninitialized", "out_of_sync"):
                st, word = "warning", "Lagging" if lag is not None and (lag >= lw or (per and lag > 2 * per)) else st_l.replace("_", " ").capitalize()
            elif transferring:
                st, word = "ok", "Transferring"
            else:
                st, word = "ok", "Healthy"
            ptxt = ""
            if transferring and prog not in (None, ""):
                ptxt = "%s moved so far" % OF.human_bytes(prog) if str(prog).isdigit() else str(prog)
            rows.append({"st": st, "word": word, "src": src or "", "dst": dst or "", "dc": c["_name"], "state": str(state), "why": str(why)[:300] or ptxt,
                         "lag": lag, "sched": sched, "per": per, "last": _iso(OF.parse_time(last))})
    rank = {"critical": 0, "warning": 1}
    rows.sort(key=lambda x: (rank.get(x["st"], 2), 0 if x["word"] == "Transferring" else 1, -(x["lag"] or 0)))
    counts = {"all": n, "broken": sum(1 for x in rows if x["st"] == "critical"), "lagging": sum(1 for x in rows if x["st"] == "warning"),
              "transferring": sum(1 for x in rows if x["word"] == "Transferring")}
    return {"rows": rows[:ROWS_MAX], "counts": counts, "errors": errs}


def _nodes(up, o):
    rows, errs = [], []
    for c in up:
        errs += _err(c, ["nodes", "failover"])
        fo = dict((r.get("node"), r) for r in _recs(c, "failover"))
        for n in _recs(c, "nodes"):
            stt = str(n.get("state") or "?")
            f = fo.get(n.get("name"), {})
            poss = OF.as_bool(f.get("possible")) if f else None
            tko = OF.g(n, "ha.takeover.state")
            if poss is None and tko in ("not_possible", "failed"):
                poss = False
            partner = f.get("partner_name") or OF.g(n, "ha.partners.name", "") or ""
            st = "critical" if stt != "up" else ("warning" if poss is False else "ok")
            rows.append({"st": st, "node": n.get("name") or "", "cluster": c["_name"], "state": stt, "partner": partner,
                         "takeover": "Yes" if poss else ("No" + (" - " + f["state_description"] if f.get("state_description") else "") if poss is False else "?"),
                         "up_s": int(n["uptime"]) if str(n.get("uptime") or "").isdigit() else None, "model": n.get("model") or ""})
    rows.sort(key=lambda x: ({"critical": 0, "warning": 1}.get(x["st"], 2), x["cluster"], x["node"]))
    return {"rows": rows[:ROWS_MAX * 4], "counts": {"all": len(rows), "down": sum(1 for x in rows if x["state"] != "up")}, "errors": errs}


def _disks(up, o):
    mn = int(o.get("min_spares", 1))
    rows, errs = [], []
    for c in up:
        errs += _err(c, ["disks"])
        per = {}
        for d in _recs(c, "disks"):
            node = OF.g(d, "home_node.name") or OF.g(d, "node.name") or "?"
            p = per.setdefault(node, {"failed": 0, "rebuild": 0, "spares": 0, "shared": 0, "total": 0, "broken": []})
            ct, stt = str(d.get("container_type") or ""), str(d.get("state") or "")
            p["total"] += 1
            p["spares"] += ct == "spare"
            p["shared"] += ct == "shared"
            if stt == "broken" or ct == "broken":
                p["failed"] += 1
                p["broken"].append(str(d.get("name") or "?"))
            elif stt in ("reconstructing", "copy"):
                p["rebuild"] += 1
        for node, p in sorted(per.items()):
            why = []
            if p["broken"]:
                why.append("%s broken" % ", ".join(p["broken"][:3]))
            if p["shared"]:
                st = "critical" if p["failed"] else "ok"
                why.append("partitioned disks (ADP): spare partitions not counted")
            elif p["failed"] or (p["spares"] == 0 and mn > 0):
                st = "critical"
                if p["spares"] == 0 and mn > 0:
                    why.append("no spare disk left")
            elif p["spares"] < mn or p["rebuild"]:
                st = "warning"
                why.append("rebuilding" if p["rebuild"] else "fewer spares than ontap_min_spares (%d)" % mn)
            else:
                st = "ok"
            rows.append({"st": st, "node": node, "cluster": c["_name"], "failed": p["failed"], "rebuild": p["rebuild"],
                         "spares": p["spares"], "shared": p["shared"], "why": "; ".join(why)})
    rows.sort(key=lambda x: ({"critical": 0, "warning": 1}.get(x["st"], 2), x["cluster"], x["node"]))
    return {"rows": rows[:ROWS_MAX * 4], "counts": {"failed": sum(x["failed"] for x in rows), "spares": sum(x["spares"] for x in rows),
                                                    "nodes": len(rows)}, "errors": errs}


def _ems(up, o, now):
    since = now - datetime.timedelta(hours=float(o.get("ems_hours", 24)))
    ignore = [re.compile(x, re.I) for x in o.get("ems_ignore") or []]
    groups, errs, left = {}, [], 0
    for c in up:
        errs += _err(c, ["ems"])
        no_ldap = OF._ldap_unused(c, o.get("ems_ignore_ldap", "auto"))     # pylint: disable=protected-access
        for e in _recs(c, "ems"):
            t = OF.parse_time(e.get("time"))
            sev = str(OF.g(e, "message.severity", "")).lower()
            name = str(OF.g(e, "message.name", ""))
            if (t and t < since) or sev not in SEV:
                continue
            if (no_ldap and "ldap" in name.lower()) or any(x.search(name) for x in ignore):
                left += 1
                continue
            k = (c["_name"], OF.g(e, "node.name", "") or "", name)
            x = groups.setdefault(k, {"n": 0, "t": None, "msg": "", "sev": sev})
            x["n"] += 1
            if t and (x["t"] is None or t >= x["t"]):
                x["t"], x["msg"] = t, str(e.get("log_message") or "")
            if SEV[sev][0] == "critical":
                x["sev"] = sev if x["sev"] == "error" or sev == "emergency" else x["sev"]
    rows = [{"t": _iso(x["t"]), "cluster": k[0], "node": k[1], "ev": k[2], "n": x["n"], "st": SEV[x["sev"]][0], "sev": SEV[x["sev"]][1],
             "msg": x["msg"][:400]} for k, x in groups.items()]
    rows.sort(key=lambda r: r["t"], reverse=True)
    return {"rows": rows[:ROWS_MAX], "counts": {"events": sum(x["n"] for x in groups.values()), "kinds": len(groups), "left_out": left,
                                                 "hours": float(o.get("ems_hours", 24))}, "errors": errs}


def _clusters(cs, nodes, disks):
    out = []
    for c in cs:
        if not c.get("reachable", True) or c.get("error"):
            out.append({"name": c.get("_name") or c.get("address"), "st": "critical", "why": "not read: %s" % str(c.get("error") or "")[:200]})
            continue
        why, st = [], "ok"
        ns = [n for n in nodes["rows"] if n["cluster"] == c["_name"]]
        if not ns:                                   # its nodes not read: not "healthy", unknown (said)
            st = "unknown"
            why.append("its nodes were not read")
        down = [n["node"] for n in ns if n["state"] != "up"]
        if down:
            st, _ = "critical", why.append("%s down" % ", ".join(down))
        noto = [n["node"] for n in ns if n["st"] == "warning"]
        if noto:
            st = OF.worst([st, "warning"])
            why.append("takeover not possible on %s" % ", ".join(noto))
        for d in [d for d in disks["rows"] if d["cluster"] == c["_name"] and d["st"] != "ok"]:
            st = OF.worst([st, d["st"]])
            why.append("%s: %s" % (d["node"], d["why"] or "disks"))
        for s in _recs(c, "subsystems"):
            if str(s.get("health") or "ok").lower() != "ok":
                st = OF.worst([st, "warning"])
                why.append("subsystem %s: %s" % (s.get("subsystem"), s.get("health")))
        info = (_recs(c, "cluster") or [{}])[0]
        out.append({"name": c["_name"], "st": st, "why": "; ".join(why)[:300], "ontap": OF.short_version(OF.g(info, "version.full"))
                    if OF.g(info, "version.full") else "", "nodes": len(ns), "up": len(ns) - len(down)})
    return out


def _perf(up, o, ds_of):
    rows, errs, n, dur = [], [], 0, ""
    for c in up:
        errs += _err(c, ["perf_volumes", "perf_luns"])
        lun_vols = set()
        for r in _recs(c, "perf_luns"):
            m = r.get("metric") or {}
            if not m:
                continue
            n += 1
            vol = OF.g(r, "location.volume.name", "") or ""
            lun_vols.add(((r.get("svm") or {}).get("name"), vol))
            rows.append(_perf_row(c, r, m, vol, str(r.get("name") or "").rsplit("/", 1)[-1], ds_of))
            dur = dur or str(m.get("duration") or "")
        for r in _recs(c, "perf_volumes"):
            m = r.get("metric") or {}
            if not m or ((r.get("svm") or {}).get("name"), r.get("name")) in lun_vols:
                continue                    # a volume of LUNs: its LUNs are the rows (a datastore is a LUN)
            n += 1
            rows.append(_perf_row(c, r, m, r.get("name") or "", "", ds_of))
            dur = dur or str(m.get("duration") or "")
    top = int(o.get("perf_top", 10))
    keep = sorted(rows, key=lambda x: -(x["lat"] or 0))[:top] + sorted(rows, key=lambda x: -(x["iops"] or 0))[:top]
    seen, out = set(), []
    for x in keep:
        k = (x["cluster"], x["svm"], x["vol"], x["lun"])
        if k not in seen:
            seen.add(k)
            lw, lc = float(o.get("lat_warn_ms", 10)), float(o.get("lat_crit_ms", 20))
            x["st"] = "critical" if (x["lat"] or 0) >= lc else ("warning" if (x["lat"] or 0) >= lw else "ok")
            out.append(x)
    return {"rows": out, "counts": {"all": n, "sample": dur}, "errors": errs}


def _perf_row(c, r, m, vol, lun, ds_of):
    lat = OF.g(m, "latency.total")
    iops = OF.g(m, "iops.total")
    thr = OF.g(m, "throughput.total")
    svm = (r.get("svm") or {}).get("name") or ""
    return {"cluster": c["_name"], "svm": svm, "vol": vol, "lun": lun, "ds": ds_of.get((c["address"].lower(), svm, vol), ""),
            "lat": round(float(lat) / 1000.0, 2) if lat is not None else None, "iops": int(float(iops)) if iops is not None else None,
            "mbs": round(float(thr) / 1048576.0, 1) if thr is not None else None, "at": _iso(OF.parse_time(m.get("timestamp")))}


def ontap_live_payload(r):
    """r: {list (site_ontap_volume action=list), choices (the survey's texts, ontap_expand_choices), found (one datastore per
    entry of list.choices, as they came), collected (site_ontap_collect of _ontap_live_queries, or {}), health, perf, opts}
    -> {clusters, volumes, aggrs, parts, not_read}"""
    lst = r.get("list") or {}
    o = r.get("opts") or {}
    ents = lst.get("choices") or []
    found = r.get("found") or []
    texts = dict(((p["address"].lower(), p["svm"], p["volume"], p["lun"]), t) for t, p in
                 ((t, X.parse_choice(t)) for t in r.get("choices") or []) if p)
    vols, ds_of = [], {}
    for i, e in enumerate(ents):
        f = found[i] if i < len(found) and isinstance(found[i], dict) else {}
        k = _key(e)
        t = texts.get(k, "")
        ds = f.get("datastore") or ""
        if ds:
            ds_of.setdefault((k[0], k[1], k[2]), ds)
        # a LUN volume is judged by its LUN (its datastore's used space, else the LUN's blocks written) - X.lun_fullness
        fsize, fused, src = X.lun_fullness(dict(e, ds_capacity=f.get("capacity"), ds_free=f.get("free")))
        vols.append({"address": e.get("address") or "", "cluster": e.get("cluster") or "", "svm": e.get("svm") or "", "volume": e.get("volume") or "",
                     "lun": e.get("lun") or "", "luns": int(e.get("luns") or 0), "size": int(e.get("size") or 0), "used": int(e.get("used") or 0),
                     "aggr": e.get("aggr") or "", "aggr_free": int(e.get("aggr_free") or 0), "datastore": ds, "ds_kind": f.get("kind") or "",
                     "full_size": int(fsize), "full_used": int(fused), "full_src": src, "thick": bool(e.get("lun_thick")),
                     "uid": e.get("lun_uuid") or e.get("uuid") or "",
                     "offered": bool(t), "why": "" if t else "not in the survey's list (ontap_expand_max_choices)", "choice": t, "root": False})
    for x in lst.get("excluded") or []:
        vols.append({"address": x.get("address") or "", "cluster": x.get("cluster") or "", "svm": "" if x.get("svm") == "(node)" else x.get("svm") or "",
                     "volume": x.get("volume") or "", "lun": "", "luns": 0, "size": int(x.get("size") or 0), "used": int(x.get("used") or 0),
                     "aggr": x.get("aggr") or "", "aggr_free": int(x.get("aggr_free") or 0), "datastore": "", "ds_kind": "", "offered": False,
                     "full_size": int(x.get("size") or 0), "full_used": int(x.get("used") or 0), "full_src": "volume", "thick": False,
                     "uid": x.get("uuid") or "",
                     "why": x.get("why") or "", "choice": "", "root": bool(x.get("root"))})
    aggrs = [{"address": a.get("address") or "", "cluster": a.get("cluster") or "", "name": a.get("name") or "", "node": a.get("node") or "",
              "size": int(a.get("size") or 0), "used": int(a.get("used") or 0)} for a in lst.get("aggregates") or []]
    clusters = [{"address": c.get("address"), "name": c.get("name"), "error": c.get("error") or ""} for c in lst.get("clusters") or []]
    not_read = ["%s: %s" % (c["address"], c["error"]) for c in clusters if c["error"]]
    parts = {}
    col = r.get("collected") or {}
    if r.get("health") or r.get("perf"):
        cs = []
        for c in col.get("clusters") or []:
            c = dict(c)
            c["_name"] = OF.g((_recs(c, "cluster") or [{}])[0], "name") or c.get("address")
            cs.append(c)
        up = [c for c in cs if c.get("reachable", True) and not c.get("error")]
        down = ["%s: %s" % (c.get("address"), c.get("error") or "not reached") for c in cs if c not in up]
        now = OF.parse_time(col.get("read_at")) or datetime.datetime.now(datetime.timezone.utc)
        if not cs:
            down = [str(col.get("msg") or "the health parts were not read")]
        if r.get("health"):
            nodes, disks = _nodes(up, o), _disks(up, o)
            parts["snapmirror"] = _snapmirror(up, o)
            parts["nodes"] = nodes
            parts["disks"] = disks
            parts["ems"] = _ems(up, o, now)
            parts["clusters"] = {"rows": _clusters(cs, nodes, disks), "counts": {}, "errors": []}
        if r.get("perf"):
            parts["perf"] = _perf(up, o, ds_of)
        for p in parts.values():
            p["errors"] = down + p["errors"]
    return {"clusters": clusters, "volumes": vols, "aggrs": aggrs, "parts": parts, "not_read": not_read}


class FilterModule(object):
    def filters(self):
        return {"ontap_live_payload": ontap_live_payload}
