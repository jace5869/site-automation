#!/usr/bin/env python3
"""1.56.0: the Low level in the VMware reports - worth knowing, nothing to do now, always with the word "Low", never a
report's (or a cluster's, a host's) status by itself. One test per moved row, on the builders of the reports' own tests:
python3 tests/test_low_vmware.py"""
import ast
import copy
import importlib.util
import os
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))


def _load(name):
    """A test module of this folder by its file, for its builders (not its tests: they run with that file)."""
    spec = importlib.util.spec_from_file_location("_low_" + name, os.path.join(HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tc = _load("test_capacity")         # DATA, OPTS, host, line (and the vCenter module set up for the filters)
tfl = _load("test_filters")         # _hl_host, _hl_cluster, VmHealth.data, VmwareAlarms.D
trs = _load("test_vm_rightsize")    # vm
tin = _load("test_vm_inventory")    # vm, READ
ttl = _load("test_vm_tools")        # vm, READ
ten = _load("test_esxi_network")    # host, vmk, read
tnp = _load("test_net_perf")        # raw
tss = _load("test_sso")             # sso

import capacity_filters as cf  # noqa: E402
import net_perf_filters as nf  # noqa: E402
import site_filters as sf  # noqa: E402
import vcenter_sso_filters as vf  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
import vmware_health_filters as vh  # noqa: E402
import vmware_hostnet_filters as hn  # noqa: E402
import vmware_inventory_filters as inv  # noqa: E402
import vmware_rightsize_filters as rz  # noqa: E402
import vmware_tools_filters as tf  # noqa: E402

GB, TB = tc.GB, tc.TB


def sec(doc, title):
    return [s for s in doc["sections"] if s["title"].startswith(title)][0]


def tile(doc, label):
    return [t for t in doc["summary"] if t["label"].startswith(label)][0]


class Capacity(unittest.TestCase):
    def report(self, d):
        return cf.vm_capacity_report(d, tc.OPTS)["report"]

    def test_runway_bands(self):
        # warn 90, crit 30: up to 30 critical, up to 90 warning, up to 180 low, after that ok
        self.assertEqual([cf.runway_status(d, 90, 30) for d in (None, 10, 30, 31, 90, 91, 180, 181)],
                         ["ok", "critical", "critical", "warning", "warning", "low", "low", "ok"])

    def test_runway_cell_says_low(self):
        rep = self.report(tc.DATA)
        ds = sec(rep, "Datastores: runway")
        row = [r for r in ds["rows"] if r[0] == "ds_prod"][0]
        i = ds["columns"].index("Runway to 90%")
        self.assertTrue(row[i].endswith(" - Low"), row[i])                       # 102 days: within 2 x 90
        self.assertEqual(ds["cell_status"][ds["rows"].index(row)][i], "low")
        # the overall storage tile: 921 days - ok, no note; at 120 days: Low, with the word in its note
        self.assertEqual(tile(rep, "Storage runway"), {"label": "Storage runway to 90%", "value": "921 days", "status": "ok"})
        d = copy.deepcopy(tc.DATA)
        d["datastores"][1].update(free=2 * TB, history=tc.line(7.7 * TB, 10 * GB))     # both 8 TB used, 20 GB a day: 102 days
        t = tile(self.report(d), "Storage runway")
        self.assertEqual(t["status"], "low", t)
        self.assertEqual(t["note"], "Low: within 180 days")

    def test_maintenance_host_row(self):
        d = copy.deepcopy(tc.DATA)
        d["clusters"][1]["hosts"][1]["maintenance"] = True
        rep = self.report(d)
        hs = sec(rep, "Hosts per cluster")
        i = [r[1] for r in hs["rows"]].index("lab2")
        self.assertEqual((hs["rows"][i][2], hs["row_status"][i], hs["cell_status"][i][2]), ("MAINTENANCE - Low", "low", "low"))
        self.assertIn("Low: in maintenance", hs["text"])

    def test_hosts_usable_below_total_low_only_for_maintenance(self):
        d = copy.deepcopy(tc.DATA)
        d["clusters"][1]["hosts"][1]["maintenance"] = True           # LAB: one host in maintenance, on purpose
        N = {n["name"]: n for n in cf.capacity_numbers(d, tc.OPTS)["clusters"]}
        self.assertEqual(N["LAB"]["status_cells"]["hosts"], "low")
        self.assertEqual(N["PROD"]["status_cells"]["hosts"], "warning")   # PROD: a host not responding stays a warning
        rep = self.report(d)
        comp = sec(rep, "Clusters: CPU and memory")
        lab = [r for r in comp["rows"] if r[0] == "LAB"][0]
        self.assertEqual(lab[2], "1 of 2 - Low")
        self.assertEqual(comp["cell_status"][comp["rows"].index(lab)][2], "low")
        self.assertNotEqual(rep["status"], "low")
        self.assertEqual(tile(rep, "Hosts usable")["status"], "warning")         # PROD's host is not responding
        d["clusters"][0]["hosts"][3]["connection"] = "connected"
        d["clusters"][0]["hosts"][3]["maintenance"] = True
        t = tile(self.report(d), "Hosts usable")
        self.assertEqual((t["status"], t["note"]), ("low", "Low: in maintenance"))

    def test_hosts_recommended_later_is_low(self):
        rep = self.report(tc.DATA)
        comp = sec(rep, "Clusters: CPU and memory")
        i = comp["columns"].index("Hosts recommended")
        prod = comp["rows"][[r[0] for r in comp["rows"]].index("PROD")]
        self.assertEqual(comp["cell_status"][[r[0] for r in comp["rows"]].index("PROD")][i], "critical")    # 1 now: unchanged
        self.assertFalse(prod[i].endswith(" - Low"))
        d = copy.deepcopy(tc.DATA)
        n = cf.capacity_numbers(d, tc.OPTS)["clusters"][0]
        later = dict(n, hosts_now=0, hosts_later=2, horizon_months=12)
        self.assertEqual(cf._lw(cf.hosts_text(later), "low"), "none now; 2 within 12 months - Low")
        # the overall Hosts row: none now, some later -> Low with the word
        ov = sec(rep, "Overall")
        self.assertEqual(ov["cell_status"][2][6], "critical")

    def test_overall_hosts_later_only(self):
        d = copy.deepcopy(tc.DATA)
        d["clusters"] = d["clusters"][1:]                         # LAB only: no host now
        d["clusters"][0]["history"]["mem"] = tc.line(100 * GB, 5 * GB)
        rep = self.report(d)
        ov = sec(rep, "Overall")
        self.assertEqual((ov["rows"][2][6], ov["cell_status"][2][6]), ("0 recommended now, 9 within 12 months - Low", "low"))
        comp = sec(rep, "Clusters: CPU and memory")
        i = comp["columns"].index("Hosts recommended")
        self.assertEqual((comp["rows"][0][i], comp["cell_status"][0][i]), ("none now; 9 within 12 months - Low", "low"))

    def test_provisioned_and_poor_fit_cells(self):
        d = copy.deepcopy(tc.DATA)
        d["datastores"][0]["uncommitted"] = 10 * TB                   # provisioned 160 %
        d["datastores"][0]["history"] = [[tc.NOW - (30 - i) * tc.DAY, (7.7 * TB) + ((i % 2) * 400 * GB) + i * 10 * GB] for i in range(31)]
        rep = self.report(d)
        ds = sec(rep, "Datastores: runway")
        r = [x for x in ds["rows"] if x[0] == "ds_prod"][0]
        cs = ds["cell_status"][ds["rows"].index(r)]
        pi, fi = ds["columns"].index("Provisioned"), ds["columns"].index("Trend fit")
        self.assertTrue(r[pi].endswith("% - Low"), r[pi])
        self.assertEqual(cs[pi], "low")
        self.assertTrue(r[fi].startswith("poor") and r[fi].endswith(" - Low"), r[fi])
        self.assertEqual(cs[fi], "low")
        st = sec(rep, "Clusters: storage")
        r = [x for x in st["rows"] if x[0] == "PROD"][0]
        self.assertTrue(r[5].endswith("% - Low"), r[5])
        self.assertEqual(st["cell_status"][st["rows"].index(r)][5], "low")


class Rightsize(unittest.TestCase):
    def test_confidence_low_and_watch(self):
        vms = [trs.vm("low1", cpus=8, mem_gb=16, cpu=9.0, mem=10.0, year_cpu=118.0, year_cpu_top=[["2026-02-28", 118.0], ["2026-03-31", 40.0]]),
               trs.vm("high1", cpus=4, mem_gb=8, cpu=10.0, mem=10.0), trs.vm("watch1", cpus=8, cpu=90.0, mem=60.0, busy_cpu=1)]
        d = rz.vm_rightsize_doc({"vcenters": [{"name": "vc01.example.mil", "ok": True}], "days": 30}, rz.vm_rightsize(vms))
        big = sec(d, "Too big")
        self.assertEqual([(r[-2], c[-2]) for r, c in zip(big["rows"], big["cell_status"])], [("High", "ok"), ("Low", "low")])
        self.assertNotIn("status", big)                                         # "Too big" stays plain
        w = sec(d, "Watch: busy on a few days")
        self.assertEqual((w["status"], w["row_status"]), ("low", ["low"]))
        self.assertTrue(w["text"].startswith("Low - worth knowing, nothing to do now."))
        self.assertEqual(tile(d, "Watch"), {"label": "Watch", "value": 1, "status": "low", "note": "Low: busy on a few days"})
        self.assertNotEqual(d["status"], "low")


class Snapshots(unittest.TestCase):
    """The snapshot report and the cleanup's report are Jinja in YAML: rendered here with the collection's filters."""

    def render(self, path, task, var, ctx):
        import jinja2
        tasks = yaml.safe_load(open(os.path.join(REPO, path)))
        t = [x for x in tasks if x.get("name") == task][0]
        env = jinja2.Environment(extensions=["jinja2.ext.do"])
        env.filters.update(sf.FilterModule().filters())
        vs = dict(ctx)
        for k in ("_vm_sn_vms", "_vm_sn_old_gb", "_vm_sn_all_gb", "_vm_report_subject"):
            if k in t.get("vars", {}):
                vs[k] = env.from_string(t["vars"][k]).render(**vs).strip()
        return ast.literal_eval(env.from_string(t["vars"][var]).render(**vs).strip())

    SNAP = {"vm": "web01", "vcenter": "vc01.example.mil", "moid": "vm-1", "id": 1, "name": "before patch", "created": "2026-10-01T06:00:00Z",
            "taken_by": "CORP\\op", "age_days": 12, "size_gb": 40.0, "folder": "/DC1/vm", "description": ""}

    def test_held_back_is_low(self):
        held = dict(self.SNAP, id=2, name="keep me", reason="its name has none of: Patching")
        ctx = {"_vm_sn": {"all": [self.SNAP, held], "old": [self.SNAP], "held": [held]}, "_vm_sn_days": "7 days", "_vm_sn_named": "",
               "_vm_sn_multi": False, "vmware_datacenter": ""}
        doc = self.render("roles/vmware_vm/tasks/snapshot_report.yml", "VMware | snapshot report", "_vm_report_doc", ctx)
        h = sec(doc, "Older than 7 days, held back")
        self.assertEqual((h["status"], h["row_status"]), ("low", ["low"]))
        self.assertTrue(h["text"].startswith("Low - worth knowing, nothing to do now."))
        self.assertEqual(sec(doc, "To delete")["status"], "warning")                # the ones to delete stay warning
        self.assertEqual(tile(doc, "Old, held back"), {"label": "Old, held back", "value": 1, "status": "low",
                                                       "note": "Low: never deleted automatically"})
        self.assertEqual(doc["status"], "warning")
        # held back only: the report stays ok
        ctx["_vm_sn"] = {"all": [held], "old": [], "held": [held]}
        doc = self.render("roles/vmware_vm/tasks/snapshot_report.yml", "VMware | snapshot report", "_vm_report_doc", ctx)
        self.assertEqual(doc["status"], "ok")


class Health(unittest.TestCase):
    def test_maintenance_admission_drs_manual_are_low(self):
        d = tfl.VmHealth().data()
        d["clusters"][0]["ha"]["admission"] = "off"
        d["clusters"][0]["drs"]["behavior"] = "manual"
        ps = [(p["severity"], p["object"], p["problem"]) for p in vh.vm_health_problems(d, {})]
        low = [(o, pr) for s, o, pr in ps if s == "low"]
        self.assertIn(("esx03", "In maintenance mode"), low)
        self.assertTrue(any(o == "CL1" and pr.startswith("HA admission control is off") for o, pr in low))
        self.assertTrue(any(o == "CL1" and pr.startswith("DRS is manual") for o, pr in low))
        # sorted after the warnings, before info
        sev = [s for s, _, _ in ps]
        self.assertEqual(sev, sorted(sev, key=lambda s: vh.RANK[s]))
        r = vh.vm_health_report(d, {})
        need = sec(r, "Needs attention")
        rows = [(x[0], x[2][:20]) for x in need["rows"]]
        self.assertIn(("LOW", "In maintenance mode"), rows)
        self.assertIn("Low = worth knowing (nothing to do now)", need["text"])
        self.assertNotIn("3 low", r["title"])

    def test_only_low_keeps_the_report_ok(self):
        d = tfl.VmHealth().data()
        d["clusters"] = [tfl._hl_cluster("CL9", ha={"admission": "off"}, drs={"behavior": "manual"})]
        d["hosts"] = [tfl._hl_host("esx91", "CL9"), tfl._hl_host("esx92", "CL9", maintenance=True, ha_state="uninitialized")]
        r = vh.vm_health_report(d, {})
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["title"].startswith("VMware health report: healthy"))
        cl = sec(r, "Clusters")
        self.assertEqual((cl["rows"][0][0], cl["row_status"][0]), ("OK", ""))             # a cluster is never Low by itself
        hs = sec(r, "Hosts")
        i = [x[1] for x in hs["rows"]].index("esx92")
        self.assertEqual((hs["rows"][i][0], hs["row_status"][i], hs["rows"][i][5], hs["cell_status"][i][5]),
                         ("LOW", "low", "yes - Low", "low"))
        self.assertEqual(tile(r, "Clusters")["status"], "ok")
        self.assertEqual(tile(r, "Hosts")["status"], "ok")

    def test_no_low_text_unchanged(self):
        d = tfl.VmHealth().data()
        d["hosts"] = [h for h in d["hosts"] if not h.get("maintenance")]
        need = sec(vh.vm_health_report(d, {}), "Needs attention")
        self.assertEqual(need["text"], "Red = critical (broken now), amber = warning (fix soon), blue = info (worth knowing, not broken).")


class Alarms(unittest.TestCase):
    def test_cleared_alarms_and_maintenance_hosts(self):
        vc = "vc01.example.mil"
        d = copy.deepcopy(tfl.VmwareAlarms.D)
        d["alarm_changes"] = [{"vcenter": "", "time": "2026-10-07T01:00:00Z", "alarm": "Host CPU usage", "entity_type": "host", "entity": "esx03",
                               "moid": "host-3", "from": "green", "to": "red", "cluster": "CL1", "datacenter": "DC1"},
                              {"vcenter": "", "time": "2026-10-07T02:00:00Z", "alarm": "Host CPU usage", "entity_type": "host", "entity": "esx03",
                               "moid": "host-3", "from": "red", "to": "green", "cluster": "CL1", "datacenter": "DC1"}]
        h = va.vm_alarm_history(d)
        self.assertEqual([(x["now"], x["severity"]) for x in h], [("cleared", "low")])
        r = va.vm_alarm_report(d, {})
        f = sec(r, "Alarms that fired")
        self.assertEqual((f["rows"][0][0], f["row_status"][0]), ("LOW", "low"))
        self.assertIn("cleared since (Low)", f["text"])
        self.assertNotIn("status", f)                                        # cleared ones: no section status
        t = tile(r, "Alarms fired")
        self.assertEqual((t["status"], t["note"]), ("low", "Low: all cleared since"))
        hs = sec(r, "All hosts")
        i = [x[0] for x in hs["rows"]].index("esx03")
        self.assertEqual((hs["row_status"][i], hs["rows"][i][4]), ("low", "yes - Low"))
        self.assertIn("Low = in maintenance mode", hs["text"])
        self.assertIn("1 alarm(s) fired and cleared", r["title"])
        self.assertNotEqual(r["status"], "low")
        self.assertTrue(vc)

    def test_no_maintenance_legend_unchanged(self):
        d = copy.deepcopy(tfl.VmwareAlarms.D)
        for h in d["hosts"]:
            h["maintenance"] = False
        hs = sec(va.vm_alarm_report(d, {}), "All hosts")
        self.assertEqual(hs["text"], "Red = not connected or a critical alarm, amber = a warning alarm, blue = in maintenance mode.")


class Inventory(unittest.TestCase):
    def test_security_tiles_low_cells_stay_warning(self):
        doc = inv.vm_inventory_doc(tin.SEC, tin.READ, {"security": True})
        t = [x for s in doc["sections"] for x in s.get("tiles") or [] if x["label"] == "Not encrypted"] or \
            [x for x in doc.get("summary") or [] if x["label"] == "Not encrypted"]
        self.assertTrue(t, "the Not encrypted tile")
        self.assertEqual((t[0]["status"], t[0]["note"]), ("low", "Low"))
        cells = [c for s in doc["sections"] for row in s.get("cell_status") or [] for c in row]
        self.assertIn("warning", cells)                                     # the security cells stay warning (STIG'd sites)

    def test_tools_upgrade_available(self):
        v = tin.vm("old01", tools_status="guestToolsNeedUpgrade")
        self.assertEqual(inv._tools(v), ("upgrade available", "low"))
        self.assertEqual(inv._tools_text(v), "upgrade available - Low")
        self.assertEqual(inv._tools_text(tin.vm("cur01", tools_status="guestToolsCurrent")), "current")


class Tools(unittest.TestCase):
    def test_upgrade_available_is_low(self):
        vms = [ttl.vm("web01"), ttl.vm("web02", status="guestToolsNeedUpgrade")]
        d = tf.vm_tools_doc(vms, ttl.READ[:1])
        t = tile(d, "Upgrade available")
        self.assertEqual((t["status"], t["note"]), ("low", "Low"))
        rows = [(r, c) for s in d["sections"] for r, c in zip(s.get("rows") or [], s.get("cell_status") or []) if "web02" in r]
        self.assertTrue(rows)
        r, c = rows[0]
        i = c.index("low")
        self.assertEqual(r[i], "upgrade available - Low")
        self.assertNotEqual(d["status"], "low")
        none = tf.vm_tools_doc([ttl.vm("web01")], ttl.READ[:1])
        self.assertNotIn("Upgrade available", [t["label"] for t in none["summary"]])             # none: no tile, as before


class HostNet(unittest.TestCase):
    def test_different_subnets_is_low(self):
        hosts = [ten.host("esx01", vmks=[ten.vmk("vmk1", "192.0.2.11", ["vmotion"])]),
                 ten.host("esx02", vmks=[ten.vmk("vmk1", "198.51.100.12", ["vmotion"])])]
        doc = hn.esxi_net_doc(ten.read(hosts))
        n = sec(doc, "Notes")
        row = [r for r in n["rows"] if "different subnets" in r[2]][0]
        self.assertEqual((row[0], n["row_status"][n["rows"].index(row)], n["status"]), ("LOW", "low", "low"))
        self.assertEqual(doc["status"], "ok")


class Sso(unittest.TestCase):
    def test_sts_within_twice_warn_days(self):
        s = tss.sso(vc={"sts": [{"subject": "CN=s", "expires": "2027-01-01", "days": 100}]})
        self.assertEqual([p["severity"] for p in vf.vcenter_sso_problems(s)], ["low"])
        self.assertEqual(vf.vcenter_sso_conditions(s), [])
        d = vf.vcenter_sso_doc({"title": "VMware health report: healthy", "status": "ok", "sections": []}, s)
        self.assertEqual((d["title"], d["status"]), ("VMware health report: healthy", "ok"))          # not counted
        st = d["sections"][0]
        self.assertEqual((st["rows"][0][0], st["row_status"][0], st["status"]), ("LOW", "low", "low"))

    def test_ocsp_severity_low(self):
        s = tss.sso(ocsp=[{"url": "http://ocsp.example.mil", "ok": False, "why": "no answer"}])
        self.assertEqual(vf.vcenter_sso_problems(s, {"ocsp_severity": "low"})[0]["severity"], "low")
        self.assertEqual(vf.vcenter_sso_conditions(s, {"ocsp_severity": "low"}), [])                 # wakes nobody
        self.assertEqual(vf.vcenter_sso_conditions(s)[0]["severity"], "critical")                    # the default unchanged
        d = vf.vcenter_sso_doc({"title": "T", "status": "ok", "sections": []}, s, {"ocsp_severity": "low"})
        self.assertEqual((d["title"], d["status"]), ("T", "ok"))
        self.assertEqual(d["sections"][0]["rows"][-1][0], "LOW")


class NetPerf(unittest.TestCase):
    def test_ocsp_severity_low(self):
        r = nf.net_perf_result(tnp.raw([], ocsp=[{"name": "OCSP A", "url": "http://ocsp.example.mil", "why": "no answer", "ms": 5}]), "h",
                               {"ocsp_severity": "low"})
        self.assertEqual([(x["id"], x["severity"]) for x in r["findings"]], [("netperf:ocsp:OCSP A", "low")])
        o = [s for s in nf.net_perf_report_sections([r]) if s["title"].startswith("OCSP")][0]
        self.assertEqual((o["row_status"], o["rows"][0][-1], o["status"]), (["low"], "Low - no answer", "ok"))
        r = nf.net_perf_result(tnp.raw([], ocsp=[{"name": "OCSP A", "url": "http://ocsp.example.mil", "why": "no answer", "ms": 5}]), "h")
        o = [s for s in nf.net_perf_report_sections([r]) if s["title"].startswith("OCSP")][0]
        self.assertEqual((o["row_status"], o["rows"][0][-1]), (["critical"], "no answer"))           # the default unchanged


if __name__ == "__main__":
    unittest.main()
