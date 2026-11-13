#!/usr/bin/env python3
"""Made-up clusters for the capacity trends report's tests (tests/test_ontap_capacity.py, tests/test_ontap_capacity_aap.py,
tests/netapp/run_capacity_db_test.sh): what the collector would read on any day. collected(now, ago) is the collector's
result ago days before now; every object's size and used space is a function of the day, so a run of the report on each
day gives a history with a known story:

  cluster1  aggr1_node03       slow growth, 71% to 73%
            aggr2_node04       82% to 87.4%: amber now, 90% in about two weeks (red)
            aggr_sas_node03    flat
            vol_home           over 85%, used space dropped five days ago, filling again: back again and filling fast
            vol_scans          over 85%, a small drop five days ago, creeping: back again only
            vol_projects       resized 4.0 TB to 4.4 TB twelve days ago: not back again (longer ago than 7 days)
            vol_vdi_profiles   68% to 84%, no change: filling fast
            vol_backup         the most GB a day, at 58%
            vol_new            made yesterday: 2 points, no rate
            vol_db_logs        snapshots at 130% of their reserve; vol_home's at 85% (near)
            vol_archive        flat; svm1_root and vol_small are left out (an SVM's root, under ontap_volume_min_gb)
  cluster2  aggr1_node05       79% to 83.5%: 90% within 90 days (amber); aggr1_node06 flat
            vol_ctx            slow growth
    python tests/netapp/capacity_fixture.py     prints the day's sizes"""
import datetime
import json
import math
import sys

GB = 1024 ** 3
TB = 1024 ** 4
NOW = "2026-11-13T06:00:05Z"


def _line(a, b, days=30.0):
    """From a % days ago to b % now, with a little day-to-day noise."""
    return lambda d: a + (b - a) * (days - d) / days + 0.08 * math.sin(d * 1.7)


def _home(d):
    return (88.6 + (30 - d) * 0.1) if d >= 5 else 79.0 + (4 - d) * 2.2


def _scans(d):
    return (86.2 + (30 - d) * 0.04) if d >= 5 else 84.6 + (4 - d) * 0.125


# name: (svm, size TB by day, used % by day, snapshot reserve %, snapshots' share of the reserve %)
VOLUMES1 = {
    "vol_home": ("svm1", lambda d: 2.0, _home, 20, lambda d: 85.0 - d * 0.5),
    "vol_scans": ("svm1", lambda d: 1.5, _scans, 0, None),
    "vol_projects": ("svm1", lambda d: 4.0 if d >= 12 else 4.4, lambda d: (84.0 - (d - 12) * 0.05) if d >= 12 else 76.4 + (11 - d) * 0.1, 5,
                     lambda d: 30.0),
    "vol_vdi_profiles": ("svm2", lambda d: 3.0, _line(68.0, 84.0), 5, lambda d: 42.0),
    "vol_backup": ("svm2", lambda d: 8.0, _line(50.0, 58.0), 0, None),
    "vol_new": ("svm1", lambda d: 0.5, lambda d: 12.0 + (1 - d) * 3.0, 5, lambda d: 0.0),
    "vol_db_logs": ("svm2", lambda d: 1.0, _line(61.0, 64.0), 5, lambda d: 130.0 - d * 1.5),
    "vol_archive": ("svm1", lambda d: 6.0, lambda d: 40.0, 0, None),
}
VOLUMES2 = {"vol_ctx": ("svm3", lambda d: 1.0, _line(60.0, 66.0), 5, lambda d: 20.0)}
AGGREGATES1 = {"aggr1_node03": ("node03", 40.0, _line(71.0, 73.0)), "aggr2_node04": ("node04", 40.0, _line(82.0, 87.4)),
               "aggr_sas_node03": ("node03", 60.0, lambda d: 61.0)}
AGGREGATES2 = {"aggr1_node05": ("node05", 24.0, _line(79.0, 83.5)), "aggr1_node06": ("node06", 24.0, lambda d: 55.0)}
BORN = {"vol_new": 1}             # days ago a volume was made


def uuid(kind, name):
    return "%s-%s-0000-4000-8000-%012x" % (kind[:4].ljust(4, "0"), name[:8].ljust(8, "0").replace("_", "0"), abs(hash_of(name)) % 16 ** 12)


def hash_of(s):
    h = 0
    for ch in s:
        h = (h * 131 + ord(ch)) % (2 ** 61 - 1)
    return h


def when(now, ago):
    t = datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - datetime.timedelta(days=ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cluster(name, address, aggregates, volumes, d, version="NetApp Release 9.14.1P3: Mon Mar 04 10:00:00 UTC 2026"):
    def part(records):
        return {"records": records, "error": "", "reduced": "", "truncated": False}
    aggrs = [{"name": n, "uuid": uuid("aggr", n), "node": {"name": node}, "state": "online",
              "space": {"block_storage": {"size": int(size * TB), "used": int(size * TB * p(d) / 100.0)}}}
             for n, (node, size, p) in sorted(aggregates.items())]
    vols = []
    for n, (svm, size, p, reserve, snap) in sorted(volumes.items()):
        if BORN.get(n, 10 ** 6) < d:
            continue
        b = int(size(d) * TB)
        v = {"name": n, "uuid": uuid("vol", n), "svm": {"name": svm}, "state": "online", "type": "rw", "is_svm_root": False,
             "space": {"size": b, "used": int(b * p(d) / 100.0), "snapshot": {"reserve_percent": reserve, "used": 0}}}
        if reserve:
            v["space"]["snapshot"]["used"] = int(b * reserve / 100.0 * snap(d) / 100.0)
        vols.append(v)
    svm = sorted(set(v[0] for v in volumes.values()))[0]
    vols.append({"name": svm + "_root", "uuid": uuid("vol", svm + "_root"), "svm": {"name": svm}, "state": "online", "type": "rw",
                 "is_svm_root": True, "space": {"size": 1 * GB, "used": int(0.97 * GB), "snapshot": {"reserve_percent": 5, "used": 0}}})
    vols.append({"name": "vol_small", "uuid": uuid("vol", name + "small"), "svm": {"name": svm}, "state": "online", "type": "rw",
                 "is_svm_root": False, "space": {"size": 10 * GB, "used": 9 * GB, "snapshot": {"reserve_percent": 0, "used": 0}}})
    return {"address": address, "reachable": True, "error": "",
            "data": {"cluster": part([{"name": name, "uuid": uuid("clus", name), "version": {"full": version}}]),
                     "aggregates": part(aggrs), "volumes": part(vols),
                     "cluster_history": {"records": [], "error": "HTTP 404: API not found", "reduced": "", "truncated": False}}}


def collected(now=NOW, ago=0, unreachable=False):
    """The collector's result (site_ontap_collect with _ontap_cap_queries) ago days before now."""
    out = {"read_at": when(now, ago), "clusters": [
        _cluster("cluster1", "cluster1.example.mil", AGGREGATES1, VOLUMES1, ago),
        _cluster("cluster2", "cluster2.example.mil", AGGREGATES2, VOLUMES2, ago)]}
    if unreachable:
        out["clusters"].append({"address": "cluster3.example.mil", "reachable": False, "data": {},
                                "error": "cannot resolve cluster3.example.mil (DNS)"})
    return out


def ontap(collected_cluster, user="svc_ro", password="readonly-test"):
    """One collected cluster as a fixture for the fake ONTAP REST API (tests/netapp/fake_ontap.py): an ONTAP without
    /api/storage/cluster/metrics (older than 9.17.1 - the fake answers "not found")."""
    d = collected_cluster["data"]
    return {"user": user, "password": password, "paths": {
        "/api/cluster": {"fields": ["name", "uuid", "version"], "single": d["cluster"]["records"][0]},
        "/api/storage/aggregates": {"fields": ["name", "uuid", "node", "state", "space"], "records": d["aggregates"]["records"]},
        "/api/storage/volumes": {"fields": ["name", "uuid", "svm", "state", "type", "is_svm_root", "space"],
                                 "records": d["volumes"]["records"]}}}


def point(c):
    """The artifact ontap_capacity a run keeps: the collected clusters' sizes (the filter's own output is tested apart)."""
    out = []
    for cl in c["clusters"]:
        if not cl.get("reachable"):
            continue
        info = cl["data"]["cluster"]["records"][0]
        ag = [{"uuid": a["uuid"], "size_b": a["space"]["block_storage"]["size"], "used_b": a["space"]["block_storage"]["used"]}
              for a in cl["data"]["aggregates"]["records"]]
        vols = [v for v in cl["data"]["volumes"]["records"] if not v["is_svm_root"] and v["space"]["size"] >= 50 * GB]
        out.append({"uuid": info["uuid"], "name": info["name"], "size_b": sum(a["size_b"] for a in ag), "used_b": sum(a["used_b"] for a in ag),
                    "aggregates": ag, "volumes": [{"uuid": v["uuid"], "size_b": v["space"]["size"], "used_b": v["space"]["used"]} for v in vols],
                    "snapshot_reserve": [{"uuid": v["uuid"], "reserve_pct": v["space"]["snapshot"]["reserve_percent"],
                                          "snap_used_b": v["space"]["snapshot"]["used"]}
                                         for v in vols if v["space"]["snapshot"]["reserve_percent"] and v["space"]["snapshot"]["used"]]})
    return {"v": 1, "read_at": c["read_at"], "clusters": out}


def jobs(now=NOW, days=30, template=88, first=4000, every=1):
    """The fake AAP's jobs (tests/netapp/fake_aap_jobs.py): one run of the template a day for days days before now, each
    with its point as the artifact ontap_capacity - and, as on a real AAP, a failed run without one, a canceled one, a
    run of an older version (no artifact), and another template's job."""
    out = []
    for d in range(days, 0, -every):
        out.append({"id": first + (days - d) * 3, "job_template": template, "status": "successful", "finished": when(now, d),
                    "artifacts": {"ontap_capacity": point(collected(now, d)), "ontap_report": {"subject": "x", "lines": []}}})
    out.append({"id": first + 1, "job_template": template, "status": "failed", "finished": when(now, days - 0.2), "artifacts": {}})
    out.append({"id": first + 2, "job_template": template, "status": "canceled", "finished": when(now, days - 0.4), "artifacts": {}})
    out.append({"id": first + 4, "job_template": template, "status": "successful", "finished": when(now, days - 1.5),
                "artifacts": {"ontap_report": {"subject": "an older version", "lines": []}}})
    out.append({"id": first + 5, "job_template": template + 1, "status": "successful", "finished": when(now, 2),
                "artifacts": {"ontap_capacity": {"v": 1, "read_at": when(now, 2), "clusters": []}}})
    return sorted(out, key=lambda j: j["id"])


if __name__ == "__main__":
    # capacity_fixture.py [AGO]                              the day's collected sizes
    # capacity_fixture.py ontap OUT.json CLUSTER(0|1) NOW    a fake_ontap.py fixture of that cluster as it is at NOW
    # capacity_fixture.py jobs OUT.json NOW DAYS             a fake_aap_jobs.py fixture: DAYS daily runs before NOW
    if len(sys.argv) > 1 and sys.argv[1] == "ontap":
        json.dump(ontap(collected(sys.argv[4])["clusters"][int(sys.argv[3])]), open(sys.argv[2], "w"))
    elif len(sys.argv) > 1 and sys.argv[1] == "jobs":
        json.dump(jobs(sys.argv[3], int(sys.argv[4])), open(sys.argv[2], "w"))
    else:
        json.dump(collected(ago=int(sys.argv[1]) if len(sys.argv) > 1 else 0), sys.stdout, indent=1)
