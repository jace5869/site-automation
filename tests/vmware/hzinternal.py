#!/usr/bin/env python3
"""Horizon's instant-clone internal VMs in vcsim, for the Horizon internal VMs report in tests/vmware/run_vmware_test.sh.

  hzinternal.py    make them, and print the golden image's IDs as JSON: {vm, snapshot, old_snapshot}

Made (vcsim has no Horizon; these are plain VMs with Horizon's names, folders and cloneprep.* settings):
  Gold/HZ-Gold                         the golden image VM, snapshots v1 and v1/v2
  ClonePrepReplicaVmFolder/cp-replica-current   its image v2 (in use when Horizon's pools name HZ-Gold / v2)
  ClonePrepReplicaVmFolder/cp-replica-old       image v1 (no pool uses it: probably left over)
  ClonePrepInternalTemplateFolder/4a1c...       a template named by a UUID only, named by cp-replica-current
  ClonePrepParentVmFolder/cp-parent-current     no golden image of its own: its replica's
  cp-replica-gone                      a golden image VM that no longer exists
  cp-parent-orphan                     orphaned (vcsim's SET.runtime.connectionState), on host DC0_C0_H1 - which is then
                                       put in maintenance mode
  W11-Desktop-001                      an instant clone desktop: cloneprep settings, but not an internal VM"""
import json
import os
import sys
import time
import urllib.parse

from pyVim.connect import SmartConnect
from pyVmomi import vim

port = int(os.environ.get("VMWARE_PORT", "18989"))
for _ in range(60):
    try:
        si = SmartConnect(host=os.environ.get("VMWARE_HOST", "127.0.0.1"), port=port, user="user", pwd="pass",
                          disableSslCertValidation=True)
        break
    except Exception:
        time.sleep(0.5)
else:
    sys.exit("vcsim not reachable")
content = si.RetrieveContent()
POD = "11110000-0000-4000-8000-000000000001"
TEMPLATE_UUID = "4a1c0000-0000-4000-8000-0000000000a1"


def wait(task):
    for _ in range(200):
        if task.info.state in ("success", "error"):
            if task.info.state == "error":
                raise RuntimeError(task.info.error.msg)
            return task.info.result
        time.sleep(0.05)
    raise RuntimeError("task timed out")


def dc0():
    return [d for d in content.rootFolder.childEntity if isinstance(d, vim.Datacenter)][0]


def folder(name):
    root = dc0().vmFolder
    for f in root.childEntity:
        if isinstance(f, vim.Folder) and f.name == name:
            return f
    return root.CreateFolder(name)


def make(name, where, extra=None, uuid=None, on=None):
    dc = dc0()
    host = [h for h in content.viewManager.CreateContainerView(dc, [vim.HostSystem], True).view if not on or h.name == on][0]
    ds = host.datastore[0]
    spec = vim.vm.ConfigSpec(name=name, numCPUs=1, memoryMB=128, guestId="otherGuest64",
                             files=vim.vm.FileInfo(vmPathName="[%s]" % ds.name),
                             extraConfig=[vim.option.OptionValue(key=k, value=v) for k, v in (extra or {}).items()])
    if uuid:
        spec.instanceUuid = uuid
    return wait(folder(where).CreateVM_Task(config=spec, pool=host.parent.resourcePool, host=host))


def meta(uuid, snap_id):
    """cloneprep.master.metadata as Omnissa's get-horizon-ic-pool-info.ps1 reads it: URL-encoded key=value pairs, the golden
    VM's instance UUID 5th, the snapshot's ID 6th (the real keys are not documented; these are made up)."""
    pairs = [("version", "1"), ("kind", "master"), ("vc", "vc"), ("dc", "DC0"), ("masterUuid", uuid), ("snapshotId", str(snap_id))]
    return urllib.parse.quote("&".join("%s=%s" % kv for kv in pairs), safe="")


def setup():
    gold = make("HZ-Gold", "Gold")
    wait(gold.CreateSnapshot_Task(name="v1", description="", memory=False, quiesce=False))
    wait(gold.CreateSnapshot_Task(name="v2", description="", memory=False, quiesce=False))
    nodes, out = gold.snapshot.rootSnapshotList, {}
    while nodes:
        out[nodes[0].name] = (nodes[0].id, nodes[0].snapshot._moId)
        nodes = nodes[0].childSnapshotList
    gu = gold.config.instanceUuid
    cur = make("cp-replica-current", "ClonePrepReplicaVmFolder",
               {"cloneprep.master.metadata": meta(gu, out["v2"][0]), "cloneprep.client.uuid": POD,
                "cloneprep.internal.template.uuid": TEMPLATE_UUID})   # public-ok: a setting
    make("4a1c0000-0000-4000-8000-0000000000a1", "ClonePrepInternalTemplateFolder", {"cloneprep.client.uuid": POD}, uuid=TEMPLATE_UUID)
    make("cp-replica-old", "ClonePrepReplicaVmFolder", {"cloneprep.master.metadata": meta(gu, out["v1"][0]), "cloneprep.client.uuid": POD})
    make("cp-parent-current", "ClonePrepParentVmFolder", {"cloneprep.replica.uuid": cur.config.instanceUuid, "cloneprep.client.uuid": POD})
    make("cp-replica-gone", "ClonePrepReplicaVmFolder",
         {"cloneprep.master.metadata": meta("42a1b2c3-0000-4000-8000-00000000dead", 1), "cloneprep.client.uuid": POD})
    orphan = make("cp-parent-orphan", "ClonePrepParentVmFolder", {"cloneprep.replica.uuid": cur.config.instanceUuid,
                                                                  "cloneprep.client.uuid": POD}, on="DC0_C0_H1")
    wait(orphan.ReconfigVM_Task(vim.vm.ConfigSpec(extraConfig=[vim.option.OptionValue(key="SET.runtime.connectionState",
                                                                                      value="orphaned")])))
    wait(orphan.runtime.host.EnterMaintenanceMode_Task(timeout=0))
    make("W11-Desktop-001", "Desktops", {"cloneprep.master.metadata": meta(gu, out["v2"][0]), "cloneprep.client.uuid": POD})
    print(json.dumps({"vm": gold._moId, "snapshot": out["v2"][1], "old_snapshot": out["v1"][1]}))


if __name__ == "__main__":
    setup()
