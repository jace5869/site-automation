#!/usr/bin/env python3
"""Unit tests for the Horizon internal VMs report: plugins/filter/horizon_internal_filters.py (how an internal VM is
recognised, its golden image read, what uses it, the verdict) and the pure parts of
roles/vmware_vm/library/site_vmware_horizon_internal.py (which VMs are read as internal, the UUIDs in a setting,
snapshot paths). The module part needs ansible-core importable; the filters need the standard library only.
    python3 tests/test_horizon_internal.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import horizon_filters as hf  # noqa: E402
import horizon_internal_filters as f  # noqa: E402

GOLD = "42a1b2c3-0000-4000-8000-000000000001"
GOLD2 = "42a1b2c3-0000-4000-8000-000000000002"
TPL = "50aa0000-0000-4000-8000-0000000000a1"
REP = "50aa0000-0000-4000-8000-0000000000b1"
POD = "11110000-0000-4000-8000-000000000001"
VC = "vc01.example.mil"


def meta(uuid, snap):
    """cloneprep.master.metadata as Omnissa's sample reads it: URL-encoded key=value pairs, the UUID 5th, the snapshot 6th."""
    pairs = [("version", "2"), ("type", "master"), ("pool", "x"), ("cluster", "c"), ("masterUuid", uuid), ("snapshotId", snap),
             ("datastore", "DS01")]
    return "%2C".join("%s%%3D%s" % kv for kv in pairs)


def ivm(name, uuid="", master=None, conn="connected", age=100.0, vc=VC, **kw):
    cp = {"cloneprep.client.uuid": POD}
    if master:
        cp["cloneprep.master.metadata"] = meta(*master)
    cp.update(kw.pop("cp", {}))
    v = {"vcenter": vc, "moid": "vm-" + name[-3:], "name": name, "uuid": uuid, "folder": "/DC1/vm/ClonePrepReplicaVmFolder",
         "folder_name": "", "found_by": ["name"], "power": "poweredOff", "connection": conn, "host": "esx01.example.mil",
         "host_maintenance": False, "datastores": ["DS01"], "committed_gb": 40.0, "created": "2026-10-01T12:00:00Z",
         "age_hours": age, "cloneprep": cp}
    v.update(kw)
    return v


def gold(moid="vm-12", uuid=GOLD, name="W11-Gold", vc=VC, snaps=None):
    return {"vcenter": vc, "moid": moid, "uuid": uuid, "name": name, "path": "/DC1/vm/Gold/" + name,
            "snapshots": snaps if snaps is not None else [{"id": 1, "moid": "snapshot-40", "name": "v2026-10", "path": "/v2026-10"},
                                                          {"id": 2, "moid": "snapshot-41", "name": "v2026-11", "path": "/v2026-10/v2026-11"}]}


def pool(pid="p1", name="IC-Win11", vm="vm-12", snap="snapshot-41", vcid="hvc1", **kw):
    p = {"id": pid, "name": name, "source": "INSTANT_CLONE", "type": "AUTOMATED", "vcenter_id": vcid,
         "provisioning_settings": {"parent_vm_id": vm, "base_snapshot_id": snap}}
    p.update(kw)
    return p


def horizon(pools=(), machines=(), farms=(), vcenters=None, errors=None, **raw):
    d = {"desktop_pools": list(pools), "machines": list(machines), "farms": list(farms),
         "vcenters": vcenters if vcenters is not None else [{"id": "hvc1", "server_name": VC}]}
    d.update(raw)
    return {"ok": True, "error": "", "data": hf.hz_data({"data": d, "errors": errors or {}})}


def run(vms, golden, hz, vcenters=(VC,), **opts):
    vc = {"internal": vms, "golden": golden, "vcenters": [{"name": n, "ok": True, "error": "", "note": ""} for n in vcenters]}
    return f.hzint_report(vc, hz, opts)


def verdicts(r):
    return dict((x["name"], (x["verdict"], x["why"])) for x in r["vms"])


class Master(unittest.TestCase):
    def test_position_as_omnissa_reads_it(self):
        m = f.hzi_master(meta(GOLD.upper(), "7"))
        self.assertEqual((m["uuid"], m["snapshot"], m["how"]), (GOLD, "7", "position"))

    def test_keys_comma_or_colon(self):
        m = f.hzi_master("golden_vm_uuid=%s,snapshot_id=snapshot-12,client=%s" % (GOLD, POD))
        self.assertEqual((m["uuid"], m["snapshot"], m["how"]), (GOLD, "snapshot-12", "keys"))
        m = f.hzi_master("masterVmUuid:%s;snapshotId:5" % GOLD)
        self.assertEqual((m["uuid"], m["snapshot"]), (GOLD, "5"))
        # a template's or the pod's UUID is never taken for the golden VM's
        m = f.hzi_master("templateUuid=%s,masterUuid=%s" % (TPL, GOLD))
        self.assertEqual(m["uuid"], GOLD)

    def test_unknown_layouts(self):
        m = f.hzi_master("a,b,c,d,%s,9,e" % GOLD)          # no keys: the 5th and 6th fields
        self.assertEqual((m["uuid"], m["snapshot"], m["how"]), (GOLD, "9", "position"))
        m = f.hzi_master("something %s else" % GOLD)
        self.assertEqual((m["uuid"], m["snapshot"], m["how"]), (GOLD, "", "any UUID"))
        self.assertEqual(f.hzi_master("")["uuid"], "")
        self.assertEqual(f.hzi_master(None)["uuid"], "")
        self.assertEqual(f.hzi_master("no uuid here")["uuid"], "")


class Kind(unittest.TestCase):
    def test_name_folder_reference(self):
        self.assertEqual(f.hzi_kind({"name": "CP-Replica-3f2a"}), "replica")
        self.assertEqual(f.hzi_kind({"name": "cp-template-1"}), "template")
        self.assertEqual(f.hzi_kind({"name": "cp-parent-9"}), "parent")
        self.assertEqual(f.hzi_kind({"name": "9a1c0d2e", "folder_name": "ClonePrepParentVmFolder"}), "parent")
        self.assertEqual(f.hzi_kind({"name": "x", "folder_name": "ClonePrepInternalTemplateFolder"}), "template")
        self.assertEqual(f.hzi_kind({"name": "x", "folder_name": "ClonePrepResyncVmFolder"}), "resync")
        rep = ivm("cp-replica-001", REP, cp={f.TEMPLATE_REF: TPL})
        self.assertEqual(f.hzi_kind({"name": "4c7e-uuid-named", "uuid": TPL, "vcenter": VC}, [rep]), "template")
        self.assertEqual(f.hzi_kind({"name": "4c7e-uuid-named", "uuid": TPL, "vcenter": "vc02"}, [rep]), "internal")
        self.assertEqual(f.hzi_kind({"name": "cp-replicas"}), "internal")


class Verdict(unittest.TestCase):
    def test_in_use_by_pool_ids(self):
        r = run([ivm("cp-replica-001", REP, (GOLD, "2"))], [gold()], horizon([pool()]))
        v = r["vms"][0]
        self.assertEqual((v["verdict"], v["used_by"], v["matched"]), ("in use", ["pool IC-Win11"], ["by ID"]))
        self.assertEqual((v["golden_vm"], v["snapshot"]), ("W11-Gold", "/v2026-10/v2026-11"))
        self.assertEqual((r["report"]["status"], r["warning"]), ("ok", 0))
        self.assertIn("none left over", r["report"]["title"])

    def test_left_over_with_reason(self):
        vms = [ivm("cp-replica-001", REP, (GOLD, "1")),                          # an older snapshot no pool uses
               ivm("cp-replica-002", "", (GOLD2, "3")),                          # its golden VM was deleted
               ivm("cp-replica-003", "", (GOLD, "77"))]                          # its snapshot was deleted
        r = run(vms, [gold()], horizon([pool()]))
        w = verdicts(r)
        self.assertEqual(w["cp-replica-001"][0], "probably left over")
        self.assertIn("no pool, farm or desktop uses golden image VM W11-Gold, snapshot /v2026-10", w["cp-replica-001"][1])
        self.assertIn("instance UUID %s) is no longer in vCenter" % GOLD2, w["cp-replica-002"][1])
        self.assertIn("its snapshot (ID 77) is no longer on golden image VM W11-Gold", w["cp-replica-003"][1])
        self.assertEqual((r["warning"], r["left_over_gb"], r["report"]["status"]), (3, 120.0, "warning"))
        self.assertIn("3 probably left over (120.0 GB)", r["report"]["title"])
        text = " ".join(s.get("text", "") for s in r["report"]["sections"])
        self.assertIn("iccleanup.cmd -vc <vcenter> -uid <user>", text)
        self.assertIn("unprotect -I <index>", text)

    def test_critical_whatever_the_use(self):
        r = run([ivm("cp-replica-001", REP, (GOLD, "2"), conn="inaccessible"), ivm("cp-parent-002", "", (GOLD, "2"), conn="orphaned")],
                [gold()], horizon([pool()]))
        w = verdicts(r)
        self.assertEqual(w["cp-replica-001"][0], "inaccessible")
        self.assertIn("used by pool IC-Win11", w["cp-replica-001"][1])
        self.assertIn("orphaned: its host no longer knows it", w["cp-parent-002"][1])
        self.assertEqual((r["critical"], r["report"]["status"]), (2, "critical"))

    def test_too_young_and_unknown_age(self):
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"), age=3.0), ivm("cp-replica-002", "", (GOLD, "1"), age=None)],
                [gold()], horizon([pool()]), min_age_hours=24)
        w = verdicts(r)
        self.assertEqual(w["cp-replica-001"][0], "not decided")
        self.assertIn("made 3.0 hour(s) ago", w["cp-replica-001"][1])
        self.assertEqual(w["cp-replica-002"][0], "probably left over")
        self.assertIn("when it was made is not known", w["cp-replica-002"][1])

    def test_horizon_not_read(self):
        hz = {"ok": "False", "error": "No Horizon connection", "data": {}}       # ansible-core 2.16 hands the bool as text
        r = run([ivm("cp-replica-001", REP, (GOLD, "1")), ivm("cp-template-002", "", (GOLD, "1"), conn="inaccessible")], [gold()], hz)
        w = verdicts(r)
        self.assertEqual(w["cp-replica-001"], ("not decided", "Horizon could not be read: whether a pool uses it is not known"))
        self.assertEqual(w["cp-template-002"][0], "inaccessible")            # still red: vCenter says so
        lines = [ln for s in r["report"]["sections"] for ln in s.get("lines", [])]
        self.assertTrue(any("Horizon could not be read (No Horizon connection)" in ln for ln in lines))
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], hz)
        self.assertEqual(r["report"]["status"], "warning")
        self.assertIn("in use not decided (Horizon not read)", r["report"]["title"])

    def test_a_part_missing_stops_left_over(self):
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([pool()], errors={"machines": "HTTP 403"}))
        self.assertEqual(verdicts(r)["cp-replica-001"][0], "not decided")
        self.assertIn("Horizon did not give the desktops (HTTP 403)", verdicts(r)["cp-replica-001"][1])

    def test_pool_without_ids_or_unknown_golden(self):
        p = pool(provisioning_settings={})
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([p]))
        self.assertIn("pool IC-Win11: Horizon did not give its golden image VM and snapshot", verdicts(r)["cp-replica-001"][1])
        # Horizon's ID is not a VM found in vCenter (and no name for it): never "left over" in that vCenter
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([pool(vm="bvm-9", snap="snap-0")]))
        w = verdicts(r)["cp-replica-001"]
        self.assertEqual(w[0], "not decided")
        self.assertIn("Horizon's ID bvm-9) is not a VM found in the vCenters read", w[1])

    def test_match_by_name_when_ids_are_not_vcenters(self):
        hz = horizon([pool(vm="bvm-2", snap="snap-2")],
                     **{"base_vms:hvc1": [{"id": "bvm-2", "name": "W11-Gold", "path": "/DC1/vm/Gold/W11-Gold"}],
                        "base_snapshots:hvc1:bvm-2": [{"id": "snap-2", "name": "v2026-11", "path": "/v2026-10/v2026-11"}]})
        r = run([ivm("cp-replica-001", REP, (GOLD, "2")), ivm("cp-replica-002", "", (GOLD, "1"))], [gold()], hz)
        w = verdicts(r)
        self.assertEqual(w["cp-replica-001"][0], "in use")
        self.assertEqual(r["vms"][0]["matched"], ["by name"])
        self.assertEqual(w["cp-replica-002"][0], "probably left over")
        lines = [ln for s in r["report"]["sections"] for ln in s.get("lines", [])]
        self.assertTrue(any("by the golden image VM's name and path" in ln and "cp-replica-001" in ln for ln in lines))

    def test_pending_image_farm_and_old_desktops_keep_it(self):
        p = pool(provisioning_status_data={"instant_clone_pending_image_parent_vm_id": "vm-12",
                                           "instant_clone_pending_image_snapshot_id": "snapshot-40"})
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([p]))
        self.assertEqual(r["vms"][0]["used_by"], ["pool IC-Win11 (its pending image)"])
        farm = {"id": "f1", "name": "RDS-Farm", "type": "AUTOMATED", "source": "INSTANT_CLONE",
                "automated_farm_settings": {"vcenter_id": "hvc1", "provisioning_settings": {"parent_vm_id": "vm-12",
                                                                                            "base_snapshot_id": "snapshot-40"}}}
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([pool()], farms=[farm]))
        self.assertEqual(r["vms"][0]["used_by"], ["farm RDS-Farm"])
        ms = [{"id": "m%d" % n, "desktop_pool_id": "p1", "managed_machine_data": {"base_vm_snapshot_id": "snapshot-40"}} for n in range(3)]
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([pool()], machines=ms))
        self.assertEqual(r["vms"][0]["used_by"], ["3 desktop(s) of pool IC-Win11"])
        # a manual farm (no image) is no reason to hold back
        r = run([ivm("cp-replica-001", REP, (GOLD, "1"))], [gold()], horizon([pool()], farms=[{"id": "f2", "name": "Man", "type": "MANUAL"}]))
        self.assertEqual(r["vms"][0]["verdict"], "probably left over")

    def test_golden_from_replica_and_parent_note(self):
        vms = [ivm("cp-replica-001", REP, (GOLD, "2"), cp={f.TEMPLATE_REF: TPL}),
               ivm("cp-template-001", TPL),                                       # no metadata: named by the replica
               ivm("cp-parent-001", "", cp={"cloneprep.replica.uuid": REP}, host="esx02.example.mil", host_maintenance=True)]
        r = run(vms, [gold()], horizon([pool()]))
        w = verdicts(r)
        self.assertEqual([w[n][0] for n in ("cp-replica-001", "cp-template-001", "cp-parent-001")], ["in use"] * 3)
        rec = dict((x["name"], x) for x in r["vms"])
        self.assertEqual((rec["cp-parent-001"]["golden_from"], rec["cp-parent-001"]["kind"]), ("cp-replica-001", "parent"))
        lines = [ln for s in r["report"]["sections"] for ln in s.get("lines", [])]
        self.assertTrue(any("cp-parent-001 is on host esx02.example.mil, which is in maintenance mode" in ln and "KB 322191" in ln
                            for ln in lines))
        r = run([ivm("cp-parent-009", "")], [gold()], horizon([pool()]))
        self.assertEqual(verdicts(r)["cp-parent-009"][0], "not decided")
        self.assertIn("no golden image recorded", verdicts(r)["cp-parent-009"][1])

    def test_other_pod(self):
        other = "22220000-0000-4000-8000-000000000002"
        vms = [ivm("cp-replica-001", REP, (GOLD, "2")), ivm("cp-replica-002", "", (GOLD, "1"), cp={"cloneprep.client.uuid": other})]
        w = verdicts(run(vms, [gold()], horizon([pool()])))
        self.assertIn("made by another Horizon pod (cloneprep.client.uuid %s)" % other, w["cp-replica-002"][1])
        w = verdicts(run(vms, [gold()], horizon([pool()]), other_pods=[other.upper()]))
        self.assertEqual(w["cp-replica-002"][0], "another pod")

    def test_vcenters(self):
        # the same moid in two vCenters: Horizon's pool is in vc01 only
        vms = [ivm("cp-replica-001", REP, (GOLD, "2")), ivm("cp-replica-002", "", (GOLD2, "2"), vc="vc02.example.mil")]
        g = [gold(), gold(uuid=GOLD2, vc="vc02.example.mil")]
        hz = horizon([pool()], vcenters=[{"id": "hvc1", "server_name": "https://VC01.example.mil:443/"}])
        w = verdicts(run(vms, g, hz, vcenters=(VC, "vc02.example.mil")))
        self.assertEqual((w["cp-replica-001"][0], w["cp-replica-002"][0]), ("in use", "probably left over"))
        # Horizon knows its vCenter by a short name: still matched; by a name the job does not know: matched in any vCenter
        hz = horizon([pool()], vcenters=[{"id": "hvc1", "server_name": "vc01"}])
        self.assertEqual(verdicts(run(vms, g, hz, vcenters=(VC, "vc02.example.mil")))["cp-replica-002"][0], "probably left over")
        hz = horizon([pool()], vcenters=[{"id": "hvc1", "server_name": "192.0.2.10"}])
        w = verdicts(run(vms, g, hz, vcenters=(VC, "vc02.example.mil")))
        # vm-12 in both vCenters: which one the pool means is not known - neither is called left over
        self.assertEqual((w["cp-replica-001"][0], w["cp-replica-002"][0]), ("not decided", "not decided"))
        g[1]["moid"] = "vm-99"
        w = verdicts(run(vms, g, hz, vcenters=(VC, "vc02.example.mil")))
        self.assertEqual((w["cp-replica-001"][0], w["cp-replica-002"][0]), ("in use", "probably left over"))

    def test_none_found_and_rows(self):
        r = run([], [], horizon([pool()]))
        self.assertEqual((r["report"]["title"], r["report"]["status"]), ("Horizon internal VMs: none found", "ok"))
        vms = [ivm("cp-replica-%03d" % n, "", (GOLD, "1")) for n in range(5)]
        r = run(vms, [gold()], horizon([pool()]), max_rows=2)
        sec = [s for s in r["report"]["sections"] if s["title"] == "Probably left over"][0]
        self.assertEqual(len(sec["rows"]), 2)
        self.assertIn("And 3 more", sec["text"])
        self.assertEqual(len(sec["rows"][0]), len(sec["columns"]))

    def test_reads_for_the_module_and_horizon(self):
        d = horizon([pool(), pool("p2", "IC-B", vm="vm-30", snap="snapshot-9", vcid="hvc2"), pool("p3", "FC", source="FULL_CLONE")])["data"]
        self.assertEqual(f.hzi_golden_ids(d), ["vm-12", "vm-30"])
        keys = [x["key"] for x in f.hzi_base_reads(d)]
        self.assertEqual(keys, ["base_vms:hvc1", "base_snapshots:hvc1:vm-12", "base_vms:hvc2", "base_snapshots:hvc2:vm-30"])
        self.assertEqual(f.hzi_golden_ids({}), [])


class Module(unittest.TestCase):
    def setUp(self):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
                HERE, "..", "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
            mod = importlib.util.module_from_spec(spec)
            sys.modules["ansible.module_utils.site_vcenters"] = mod
            spec.loader.exec_module(mod)
            sys.path.insert(0, os.path.join(HERE, "..", "roles", "vmware_vm", "library"))
            import site_vmware_horizon_internal
        except ImportError:                     # no ansible-core here: the filters are tested, the module is not
            self.skipTest("ansible-core is not importable")
        self.m = site_vmware_horizon_internal

    def test_candidate(self):
        self.assertEqual(self.m.candidate("CP-REPLICA-1", "ClonePrepReplicaVmFolder"), ["name", "folder"])
        self.assertEqual(self.m.candidate("2f1a9c", "ClonePrepParentVmFolder"), ["folder"])
        self.assertEqual(self.m.candidate("cp-replicas", "vm"), [])
        self.assertEqual(self.m.candidate("W11-Gold", "Gold"), [])

    def test_uuids_in_and_snapshots(self):
        self.assertEqual(self.m.uuids_in(meta(GOLD.upper(), "7")), [GOLD])
        self.assertEqual(self.m.uuids_in("%s,%s,%s" % (GOLD, TPL, GOLD)), [GOLD, TPL])
        self.assertEqual(self.m.uuids_in(None), [])
        tree = [(1, "snapshot-40", "a", [(2, "snapshot-41", "b", [])]), (3, "snapshot-42", "c", [])]
        self.assertEqual([x["path"] for x in self.m.snapshot_paths(tree)], ["/a", "/a/b", "/c"])
        # what the module reads agrees with the filter's reading of the same setting
        self.assertEqual(self.m.uuids_in(meta(GOLD, "7"))[0], f.hzi_master(meta(GOLD, "7"))["uuid"])


if __name__ == "__main__":
    unittest.main()
