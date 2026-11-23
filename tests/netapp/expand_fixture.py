#!/usr/bin/env python3
"""The fake cluster of tests/netapp/run_expand_test.sh (1.123.0, NetApp - expand a volume), for tests/netapp/fake_ontap.py.

  expand_fixture.py OUT.json USER PASSWORD [race|jobfail]

cluster1: aggr1 (20 TB, 71.2 % used), aggr2 (30 TB, 180 GB under 97 %). Volumes:
  vol_ds01   svm_vmware, 10 GB, 88 % used, one LUN lun_ds01 (9 GB, serial 80Ab1DmT5zXq)
  vol_ds03   svm_vmware, 2 TB, two LUNs lun_a and lun_b
  vol_nfs02  svm_vmware, 500 GB, NFS export /vol_nfs02 (SVM address 192.0.2.21)
  vol_sql01  svm1 on aggr2, 2 TB, 91 % used (the default 204.8 GB does not fit, 180 GB does)
  vol_home   svm1, 1.5 TB;  vol_bk01 svm_backup (excluded by the tests' ontap_expand_exclude)
  never: svm1_root (SVM root), vol_sql01_dr (DP), vol_old (offline), fg_data (FlexGroup)
race: vol_home is 5 GB bigger from its third read on (someone grew it between the plan and the change).
jobfail: ONTAP's jobs fail."""
import json
import sys

GB, TB = 1024 ** 3, 1024 ** 4
out, user, pw = sys.argv[1:4]
mode = sys.argv[4] if len(sys.argv) > 4 else ""


def vol(uuid, name, svm, aggrs, size, used, **kw):
    v = {"uuid": uuid, "name": name, "svm": {"name": svm}, "aggregates": [{"name": a} for a in aggrs], "state": "online", "type": "rw",
         "style": "flexvol", "is_svm_root": False, "space": {"size": int(size), "used": int(used)}, "comment": ""}
    v.update(kw)
    return v


def lun(uuid, name, svm, volume, size, serial):
    return {"uuid": uuid, "name": name, "svm": {"name": svm}, "location": {"volume": {"name": volume}}, "space": {"size": int(size)},
            "serial_number": serial}


vols = [vol("v-ds01", "vol_ds01", "svm_vmware", ["aggr1"], 10 * GB, 8.8 * GB, comment="app team datastore"),
        vol("v-ds03", "vol_ds03", "svm_vmware", ["aggr1"], 2 * TB, 1.58 * TB),
        vol("v-nfs02", "vol_nfs02", "svm_vmware", ["aggr1"], 500 * GB, 470 * GB, nas={"path": "/vol_nfs02"}),
        vol("v-sql01", "vol_sql01", "svm1", ["aggr2"], 2 * TB, 1.82 * TB),
        vol("v-home", "vol_home", "svm1", ["aggr1"], 1.5 * TB, 0.96 * TB),
        vol("v-bk01", "vol_bk01", "svm_backup", ["aggr1"], 4 * TB, 3 * TB),
        vol("v-root", "svm1_root", "svm1", ["aggr1"], 1 * GB, 0.1 * GB, is_svm_root=True),
        vol("v-dr", "vol_sql01_dr", "svm_dr", ["aggr1"], 2 * TB, 1.8 * TB, type="dp"),
        vol("v-old", "vol_old", "svm1", ["aggr1"], 100 * GB, 0, state="offline"),
        vol("v-fg", "fg_data", "svm2", ["aggr1", "aggr2"], 10 * TB, 5 * TB, style="flexgroup")]
aggr2_size = 30 * TB
fixture = {
    "user": user, "password": pw, "writable": True,
    "paths": {
        "/api/cluster": {"fields": ["name", "uuid", "version"], "single": {"name": "cluster1", "uuid": "c-1"}},
        "/api/storage/volumes": {"fields": ["uuid", "name", "svm", "aggregates", "state", "type", "style", "is_svm_root", "space", "nas", "comment"],
                                 "records": vols},
        "/api/storage/aggregates": {"fields": ["uuid", "name", "space"], "records": [
            {"uuid": "a-1", "name": "aggr1", "space": {"block_storage": {"size": 20 * TB, "used": int(14.24 * TB)}}},
            {"uuid": "a-2", "name": "aggr2", "space": {"block_storage": {"size": aggr2_size, "used": int(aggr2_size * 0.97) - 180 * GB}}}]},
        "/api/storage/luns": {"fields": ["uuid", "name", "svm", "location", "space", "serial_number"], "records": [
            lun("l-ds01", "/vol/vol_ds01/lun_ds01", "svm_vmware", "vol_ds01", 9 * GB, "80Ab1DmT5zXq"),
            lun("l-a", "/vol/vol_ds03/lun_a", "svm_vmware", "vol_ds03", 900 * GB, "80Ab1DmT5zXa"),
            lun("l-b", "/vol/vol_ds03/lun_b", "svm_vmware", "vol_ds03", 900 * GB, "80Ab1DmT5zXb")]},
        "/api/network/ip/interfaces": {"fields": ["svm", "ip", "scope"], "records": [
            {"svm": {"name": "svm_vmware"}, "ip": {"address": "192.0.2.21"}, "scope": "svm"}]},
    },
}
if mode == "race":
    fixture["grow_on_read"] = {"v-home": 5 * GB}
    fixture["grow_on_read_at"] = 3          # the plan's read, the change's own plan, then its re-check right before the change
if mode == "jobfail":
    fixture["job_state"] = "failure"
json.dump(fixture, open(out, "w"), indent=1)
