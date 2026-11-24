#!/usr/bin/env python3
"""Made-up NetApp data for the portal's NetApp page (tests/test_netapp_live.py and the page's pictures): two clusters, what
NetApp - refresh volume list's module returns (site_ontap_volume action=list), the datastores vCenter gave, and what
site_ontap_collect returns for the health and performance queries (_ontap_live_health_queries, _ontap_live_perf_queries).
Placeholders only. run(hours_ago) is the list as it was then: each volume's used space grows by its own rate a day."""
import datetime

GB = 1024 ** 3
TB = 1024 ** 4

C1, C2 = "cluster1.example.mil", "cluster2.example.mil"
# (address, svm, volume, size GB, used GB now, aggr, GB a day, datastore, kind, luns)
VOLS = [
    (C1, "svm_a", "vol_ds03", 2048, 1974, "aggr1_c1n1", 18.2, "DS-SITEA-03", "VMFS", 1),
    (C2, "svm_b", "vol_ds07", 1024, 975, "aggr2_c2n2", 6.1, "DS-SITEA-07", "NFS", 0),
    (C1, "svm_a", "vol_ds01", 4096, 3727, "aggr1_c1n1", 22.5, "DS-SITEA-01", "VMFS", 2),
    (C2, "svm_b", "vol_sql02", 1024, 908, "aggr1_c2n1", 3.4, "DS-SITEA-05", "VMFS", 1),
    (C2, "svm_b", "vol_home", 2048, 1765, "aggr1_c2n1", 0.4, "", "", 0),
    (C1, "svm_a", "vol_ds04", 4096, 2949, "aggr1_c1n2", 1.2, "DS-SITEA-04", "VMFS", 1),
    (C1, "svm_a", "vol_ds05", 2048, 1311, "aggr1_c1n2", -2.1, "DS-SITEA-06", "NFS", 0),
    (C2, "svm_b", "vol_app01", 512, 210, "aggr1_c2n1", 0.0, "", "", 0),
]
NEW = (C2, "svm_b", "vol_new01", 1024, 420, "aggr1_c2n1", 2.0, "DS-SITEA-08", "VMFS", 1)       # made 2 h ago: no rate yet
EXCL = [
    (C2, "svm_b_dr", "vol_ds02_dst", 2048, 1841, "aggr2_c2n2", 9.0, "DP volume (a SnapMirror destination) - always", False),
    (C1, "", "vol0", 300, 290, "aggr0_c1n1", 0.0, "root volume of a node - always", True),
]
AGGRS = [(C2, "aggr2_c2n2", "cluster2-02", 21.6, 91.2, 28), (C1, "aggr1_c1n1", "cluster1-01", 36.8, 87.5, 41),
         (C2, "aggr1_c2n1", "cluster2-01", 21.6, 70.4, 12), (C1, "aggr1_c1n2", "cluster1-02", 36.8, 63.0, 19)]


def uid(vol, lun=0):
    """a UUID-like id of a volume (or of its LUN) - hex and dashes, as ONTAP's"""
    import hashlib
    h = hashlib.sha1(("%s/%d" % (vol, lun)).encode()).hexdigest()
    return "%s-%s-%s-%s-%s" % (h[:8], h[8:12], h[12:16], h[16:20], h[20:32])


def name(addr):
    return addr.split(".")[0]


def at(hours_ago, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
    return now - datetime.timedelta(hours=hours_ago)


def listed(hours_ago=0.0, new=True):
    """site_ontap_volume action=list as it read hours_ago hours ago, and the datastores (found) of its entries."""
    choices, found, excluded = [], [], []
    aggr_free = dict(((a[0], a[1]), int(a[3] * TB * (1 - a[4] / 100.0) + a[5] * GB * hours_ago)) for a in AGGRS)
    for v in VOLS + ([NEW] if new else []):
        addr, svm, vol, size, used, aggr, rate, ds, kind, luns = v
        u = int((used - rate * hours_ago / 24.0) * GB)
        base = {"cluster": name(addr), "address": addr, "svm": svm, "volume": vol, "size": size * GB, "used": u, "aggr": aggr,
                "aggr_free": aggr_free.get((addr, aggr), 5 * TB), "datastore": "", "nfs_path": "" if luns else "/" + vol,
                "nfs_ips": ["192.0.2.21"], "lun": "", "luns": 0, "naa": "", "uuid": uid(vol)}
        for i in range(max(1, luns)):
            e = dict(base)
            if luns:
                e.update(lun="/vol/%s/lun%d" % (vol, i + 1), luns=luns, naa="naa.600a0980%024d" % (len(choices) + 1),
                         lun_size=size * GB // luns, lun_uuid=uid(vol, i + 1), lun_used=u // luns, lun_thick=vol != "vol_sql02")
            choices.append(e)
            name_ = ds if i == 0 else ds + "-B"
            f = {"datastore": name_, "kind": kind} if ds else {}
            if ds and luns and vol != "vol_new01":                 # vCenter's own capacity and free space (vol_new01: none - the LUN's written)
                f.update(capacity=size * GB // luns, free=size * GB // luns - u // luns)
            found.append(f)
    for addr, svm, vol, size, used, aggr, rate, why, root in EXCL:
        excluded.append({"volume": vol, "svm": svm or "(node)", "cluster": name(addr), "why": why, "address": addr, "size": size * GB,
                         "used": int((used - rate * hours_ago / 24.0) * GB), "aggr": aggr, "aggr_free": 2 * TB, "uuid": uid(vol), "root": root})
    aggrs = [{"cluster": name(a[0]), "address": a[0], "name": a[1], "node": a[2], "size": int(a[3] * TB),
              "used": int(a[3] * TB * a[4] / 100.0 - a[5] * GB * hours_ago / 24.0)} for a in AGGRS]
    return {"clusters": [{"address": C1, "name": "cluster1", "reachable": True, "error": ""},
                         {"address": C2, "name": "cluster2", "reachable": True, "error": ""}],
            "choices": choices, "excluded": excluded, "aggregates": aggrs}, found


def _iso(hours_ago):
    return at(hours_ago).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def collected():
    """site_ontap_collect's answer for _ontap_live_health_queries + _ontap_live_perf_queries."""
    def recs(r):
        return {"records": r, "error": "", "reduced": False, "truncated": False}

    def node(n, model, up_days, partner):
        return {"name": n, "state": "up", "uptime": up_days * 86400, "model": model, "ha": {"partners": [{"name": partner}]}}

    def sm(src, dst, state, status, healthy, lag, last_h, sched, why="", err="", prog=None):
        return {"source_path": src, "destination_path": dst, "state": state, "status": status, "healthy": healthy, "lag_time": lag,
                "last_transfer_end_timestamp": _iso(last_h), "unhealthy_reason": why, "last_transfer_error": err, "schedule": sched,
                "total_progress": prog, "policy": "MirrorAllSnapshots"}

    def ems(h, n, sev, ev, msg):
        return {"time": _iso(h), "node": {"name": n}, "message": {"name": ev, "severity": sev}, "log_message": msg}

    def perf(name_, svm, lat_ms, iops, mbs, vol=None):
        r = {"name": name_, "svm": {"name": svm}, "metric": {"latency": {"total": int(lat_ms * 1000)}, "iops": {"total": iops},
                                                             "throughput": {"total": int(mbs * 1048576)}, "duration": "PT15S",
                                                             "timestamp": _iso(0.02)}}
        if vol:
            r["location"] = {"volume": {"name": vol}}
        return r
    scheds = recs([{"name": "hourly", "type": "cron", "cron": {"minutes": [5]}}, {"name": "daily", "type": "cron", "cron": {"minutes": [0], "hours": [0]}}])
    c1 = {"address": C1, "reachable": True, "error": "", "data": {
        "cluster": recs([{"name": "cluster1", "version": {"full": "NetApp Release 9.14.1P3: Mon Jan 01 2026"}}]),
        "nodes": recs([node("cluster1-01", "AFF-A400", 212, "cluster1-02"), node("cluster1-02", "AFF-A400", 212, "cluster1-01"),
                       node("cluster1-03", "AFF-A400", 98, "cluster1-04"), node("cluster1-04", "AFF-A400", 98, "cluster1-03")]),
        "failover": recs([{"node": n, "partner_name": p, "possible": "true"} for n, p in (("cluster1-01", "cluster1-02"), ("cluster1-02", "cluster1-01"),
                                                                                           ("cluster1-03", "cluster1-04"), ("cluster1-04", "cluster1-03"))]),
        "subsystems": recs([{"subsystem": "sas_connect", "health": "ok"}]),
        "ems": recs([ems(8.1, "cluster1-02", "error", "net.ifgrp.lacp.link.inactive", "Port e0e of ifgrp a0a is inactive."),
                     ems(8.0, "cluster1-02", "error", "net.ifgrp.lacp.link.inactive", "Port e0e of ifgrp a0a is inactive."),
                     ems(8.05, "cluster1-01", "alert", "monitor.volume.nearlyFull", "Volume vol_ds03@vserver:svm_a is nearly full (96 % used)."),
                     ems(30, "cluster1-01", "error", "old.event", "older than the window")]),
        "ldap": recs([{"svm": {"name": "svm_a"}, "servers": ["192.0.2.30"]}]),
        "disks": recs([{"name": "1.0.%d" % i, "state": "present", "container_type": "spare" if i < 4 else "aggregate",
                        "home_node": {"name": "cluster1-0%d" % (1 + i % 4)}} for i in range(16)]),
        "snapmirror_cli": recs([sm("svm_b:vol_home", "svm_a_dr:vol_home_dst", "Snapmirrored", "Idle", "true", "9:15:00", 9.3, "daily")]),
        "schedules": scheds,
        "perf_volumes": recs([perf("vol_ds05", "svm_a", 3.1, 1200, 40), perf("vol_ds03", "svm_a", 6.2, 4180, 95)]),
        "perf_luns": recs([perf("/vol/vol_ds03/lun1", "svm_a", 6.2, 4180, 95, "vol_ds03"), perf("/vol/vol_ds01/lun1", "svm_a", 3.1, 12850, 401, "vol_ds01"),
                           perf("/vol/vol_ds04/lun1", "svm_a", 1.9, 6020, 140, "vol_ds04")])}}
    c2 = {"address": C2, "reachable": True, "error": "", "data": {
        "cluster": recs([{"name": "cluster2", "version": {"full": "NetApp Release 9.14.1P3: Mon Jan 01 2026"}}]),
        "nodes": recs([node("cluster2-01", "AFF-A250", 41, "cluster2-02"), node("cluster2-02", "AFF-A250", 41, "cluster2-01")]),
        "failover": recs([{"node": "cluster2-01", "partner_name": "cluster2-02", "possible": "true"},
                          {"node": "cluster2-02", "partner_name": "cluster2-01", "possible": "true"}]),
        "subsystems": recs([{"subsystem": "sas_connect", "health": "ok"}]),
        "ems": recs([ems(0.3, "cluster2-02", "error", "wafl.vol.autoSize.fail", "Unable to grow volume vol_ds07@vserver:svm_b: it is at its maximum autosize."),
                     ems(0.5, "cluster2-02", "error", "wafl.vol.autoSize.fail", "Unable to grow volume vol_ds07@vserver:svm_b: it is at its maximum autosize."),
                     ems(2.2, "cluster2-02", "error", "sms.status.out.of.sync", "Destination svm_b_dr:vol_ds03_dst is out of sync with svm_a:vol_ds03: transfer failed."),
                     ems(15.6, "cluster2-01", "error", "raid.rg.spares.low", "aggr1_c2n1/plex0/rg0: no spare disk left of this type on cluster2-01."),
                     ems(15.7, "cluster2-01", "alert", "disk.failmsg", "Disk 1.0.14 failed and was taken out of aggr1_c2n1."),
                     ems(3.0, "cluster2-01", "error", "secd.ldap.noServers", "no LDAP server - left out: no SVM has an LDAP client")]),
        "ldap": recs([]),
        "disks": recs([{"name": "1.0.14", "state": "broken", "container_type": "broken", "home_node": {"name": "cluster2-01"}},
                       {"name": "1.0.15", "state": "reconstructing", "container_type": "aggregate", "home_node": {"name": "cluster2-01"}}]
                      + [{"name": "1.1.%d" % i, "state": "present", "container_type": "spare" if i < 2 else "aggregate",
                          "home_node": {"name": "cluster2-02"}} for i in range(8)]),
        "snapmirror_cli": recs([
            sm("svm_a:vol_ds02", "svm_b_dr:vol_ds02_dst", "Broken-off", "Idle", "false", "76:00:00", 76, "hourly", why="Broken off by hand"),
            sm("svm_a:vol_ds03", "svm_b_dr:vol_ds03_dst", "Snapmirrored", "Idle", "true", "26:00:00", 26, "hourly", err="no space on aggr2_c2n2"),
            sm("svm_a:vol_sql01", "svm_b_dr:vol_sql01_dst", "Snapmirrored", "Transferring", "true", "1:40:00", 1.7, "hourly", prog="26 GB"),
            sm("svm_a:vol_ds01", "svm_b_dr:vol_ds01_dst", "Snapmirrored", "Idle", "true", "0:35:00", 0.6, "hourly")]),
        "schedules": scheds,
        "perf_volumes": recs([perf("vol_ds07", "svm_b", 22.4, 2110, 48), perf("vol_home", "svm_b", 1.2, 880, 22)]),
        "perf_luns": recs([perf("/vol/vol_sql02/lun1", "svm_b", 14.8, 9420, 312, "vol_sql02")])}}
    return {"read_at": at(0).strftime("%Y-%m-%dT%H:%M:%SZ"), "clusters": [c1, c2]}


OPTS = {"volume_warn_pct": 85.0, "volume_crit_pct": 90.0, "aggr_warn_pct": 85.0, "aggr_crit_pct": 90.0, "aggr_max_pct": 97.0,
        "days_warn": 30.0, "days_crit": 7.0, "list_days": 90.0, "min_points": 3, "min_hours": 6.0, "growth_days": 7.0, "min_gb": 50.0,
        "lag_warn_hours": 24.0, "lag_crit_hours": 48.0, "min_spares": 1, "ems_hours": 24.0, "ems_ignore": [], "ems_ignore_ldap": "auto",
        "lat_warn_ms": 10.0, "lat_crit_ms": 20.0, "perf_top": 10, "health": True, "perf": True, "vmware": True, "vc_error": ""}
