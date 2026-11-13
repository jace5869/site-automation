#!/usr/bin/env python3
"""Unit tests for vSphere capacity planning (plugins/filter/capacity_filters.py and the pure parts of
roles/vmware_vm/library/site_vmware_capacity.py), on fixed numbers: python3 tests/test_capacity.py"""
import json
import os
import re
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "vmware_vm", "library"))
# the modules import their shared vCenter code as Ansible does: from ansible.module_utils.site_vcenters
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_vcenters", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "roles", "vmware_vm", "module_utils", "site_vcenters.py"))
site_vcenters = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_vcenters"] = site_vcenters
_spec.loader.exec_module(site_vcenters)
import capacity_filters as cf  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
import site_vmware_capacity as sc  # noqa: E402

GB, TB, DAY = 1024 ** 3, 1024 ** 4, 86400
B = 3                     # 1.57.0: the runway board's three sections come first
NOW = 1790000000          # read_at below
READ = "2026-09-21T14:13:20Z"


def line(start, per_day, days=30, end=NOW):
    return [[end - (days - i) * DAY, start + per_day * i] for i in range(days + 1)]


def host(name, mem_gb, used_gb, cores=32, mhz=2500, used_mhz=20000, conn="connected", maint=False, vms=10):
    return {"name": name, "connection": conn, "maintenance": maint, "cores": cores, "cpu_mhz": cores * mhz, "mem_bytes": mem_gb * GB,
            "cpu_used_mhz": used_mhz, "mem_used_bytes": used_gb * GB, "vms_on": vms}


DATA = {"vcenter": "vc01", "read_at": READ, "history_days": 90, "events_days": 30, "notes": [],
        "clusters": [{"name": "PROD", "datacenter": "DC1", "standalone": False, "ha": {"enabled": True, "kind": "hosts", "hosts": 1},
                      "hosts": [host("esx1", 512, 400), host("esx2", 512, 400), host("esx3", 512, 400), host("esx4", 512, 0, conn="notResponding")],
                      "vms_on": 30, "vms_off": 2, "vcpus_on": 120, "mem_configured_on": 1500 * GB, "added": 6, "removed": 1,
                      "history": {"mem": line(1100 * GB, 4 * GB), "cpu": line(50000, 100)}},
                     {"name": "LAB", "datacenter": "DC1", "standalone": False, "ha": {"enabled": False},
                      "hosts": [host("lab1", 256, 50, used_mhz=5000), host("lab2", 256, 50, used_mhz=5000)],
                      "vms_on": 10, "vms_off": 0, "vcpus_on": 20, "mem_configured_on": 200 * GB, "added": 0, "removed": 0,
                      "history": {"mem": line(100 * GB, 0), "cpu": line(10000, -10)}}],
        "datastores": [{"name": "ds_prod", "datacenter": "DC1", "clusters": ["PROD"], "hosts": 4, "shared": True, "pod": "", "type": "VMFS",
                        "accessible": True, "capacity": 10 * TB, "free": 2 * TB, "uncommitted": 4 * TB, "history": line(7.7 * TB, 10 * GB)},
                       {"name": "ds_lab", "datacenter": "DC1", "clusters": ["LAB"], "hosts": 2, "shared": True, "pod": "", "type": "NFS",
                        "accessible": True, "capacity": 10 * TB, "free": 9 * TB, "uncommitted": 0, "history": line(1 * TB, 0)},
                       {"name": "local1", "datacenter": "DC1", "clusters": ["LAB"], "hosts": 1, "shared": False, "capacity": TB, "free": 0,
                        "uncommitted": 0, "history": []}]}
OPTS = {"trend_days": 30, "min_samples": 7, "events_days": 30, "failover_hosts": 1, "mem_target_pct": 80, "cpu_target_pct": 80,
        "vcpu_per_core_max": 4, "storage_warn_pct": 85, "storage_crit_pct": 90, "runway_warn_days": 90, "runway_crit_days": 30, "max_rows": 100}


class Trend(unittest.TestCase):
    def test_straight_line(self):
        t = cf.trend(line(100.0, 2.0), 30, NOW, 7)
        self.assertAlmostEqual(t["slope"], 2.0, places=6)
        self.assertEqual((t["r2"], t["n"], t["span"]), (1.0, 31, 30.0))

    def test_window_and_too_little(self):
        self.assertEqual(cf.trend(line(0, 1.0, days=90), 30, NOW, 7)["n"], 31)        # only the last 30 days
        self.assertIsNone(cf.trend(line(0, 1.0, days=4), 30, NOW, 7))                 # 5 samples < 7
        self.assertIsNone(cf.trend([[NOW, 1], [NOW - DAY, 2]] * 5, 30, NOW, 7))       # under 3 days
        self.assertIsNone(cf.trend([], 30, NOW))
        noisy = cf.trend([[NOW - i * DAY, (i % 2) * 100.0] for i in range(20)], 30, NOW, 7)
        self.assertLess(noisy["r2"], 0.1)

    def test_days_until_and_texts(self):
        self.assertEqual(cf.days_until(50, 5, 100), 10)
        self.assertEqual(cf.days_until(100, 5, 100), 0)
        self.assertIsNone(cf.days_until(50, 0, 100))
        self.assertIsNone(cf.days_until(50, -1, 100))
        self.assertIsNone(cf.days_until(0, 5, 0))                                     # no capacity: no runway
        self.assertEqual(cf.runway_text(None), "not growing")
        self.assertEqual(cf.runway_text(0), "reached")
        self.assertTrue(cf.runway_text(10).startswith("10 days ("))
        self.assertEqual(cf.runway_text(10 ** 9), "10+ years")                    # past year 9999: no date, no crash
        self.assertEqual(cf.fit_text({"r2": 0.91, "span": 30.0}), "good (R2 0.91, 30 days)")


class Numbers(unittest.TestCase):
    def setUp(self):
        self.N = cf.capacity_numbers(DATA, OPTS)
        self.prod = self.N["clusters"][0]
        self.lab = self.N["clusters"][1]

    def test_n_plus_one(self):
        p = self.prod
        self.assertEqual((p["hosts_usable"], p["hosts_total"], p["failover_hosts"]), (3, 4, 1))   # esx4 not responding
        self.assertEqual((p["mem"], p["mem_n1"], p["mem_used"]), (1536 * GB, 1024 * GB, 1200 * GB))
        self.assertEqual((p["mem_pct"], p["mem_n1_pct"]), (78.1, 117.2))
        self.assertEqual(p["vcpu_ratio"], round(120 / 64.0, 2))                                  # cores left with one host failed
        self.assertEqual(p["mem_days"], 0)                                                       # past the target already
        self.assertEqual(p["status"], "critical")
        self.assertEqual(p["status_cells"]["n1"], "critical")
        self.assertEqual(p["status_cells"]["hosts"], "warning")

    def test_hosts_recommended(self):
        p = self.prod
        # memory: 1200 GB used must be <= 80 % of what is left: 1500 GB needed, 1024 GB left -> 476 GB -> 1 host of 512 GB
        self.assertEqual(p["hosts_now"], 1)
        # in 12 months at +4 GB a day: 1200 + 4 * 365 GB -> 3324 GB needed -> 5 hosts
        self.assertEqual(p["hosts_later"], 5)
        self.assertEqual(p["mem_avg_after"], 58.6)
        self.assertEqual(cf.hosts_text(p), "1 now (memory 59% on average then); 5 within 12 months")
        self.assertEqual(cf.hosts_text(self.lab), "none within 12 months")
        self.assertEqual(cf.hosts_for(100, 100, 50, 0.8), 1)
        self.assertEqual(cf.hosts_for(10, 100, 50, 0.8), 0)
        self.assertIsNone(cf.hosts_for(10, 100, 0, 0.8))
        ov = self.N["overall"]
        self.assertEqual((ov["hosts_now"], ov["hosts_later"]), (1, 5))

    def test_room_and_limit(self):
        l = self.lab
        # memory: 256 GB left after one host fails, 80 % = 204.8 GB, 100 GB used, 10 GB used per VM -> 10 more
        self.assertEqual(l["fits"]["memory"], 10)
        self.assertEqual(l["fits"]["vCPUs per core"], 54)                                       # (32 * 4 - 20) / 2
        self.assertEqual((l["fit"], l["limit"]), (10, "memory"))
        self.assertIsNone(l["mem_days"])                                                         # flat: not growing
        self.assertEqual(l["net_per_month"], 0)
        self.assertEqual(self.prod["net_per_month"], 5.0)

    def test_storage_and_overall(self):
        ds = {d["name"]: d for d in self.N["datastores"]}
        self.assertEqual(ds["ds_prod"]["pct"], 80.0)
        self.assertEqual(ds["ds_prod"]["prov_pct"], 120.0)
        self.assertEqual(ds["ds_prod"]["days_warn"], 51)          # (8.5 TB - 8 TB) / 10 GB a day
        self.assertEqual(ds["ds_prod"]["days_crit"], 102)
        self.assertEqual(ds["ds_prod"]["status"], "low")          # 1.56.0: 102 days, within twice the 90 warn days
        self.assertFalse(ds["local1"]["shared"])
        ov = self.N["overall"]
        self.assertEqual(ov["storage"]["capacity"], 20 * TB)      # the local datastore is not counted
        self.assertEqual(ov["hosts"], 6)
        self.assertEqual(ov["vms_on"], 40)
        self.assertEqual(self.lab["storage"]["datastores"], 1)


class Limits(unittest.TestCase):
    def test_limit_and_runway_text(self):
        self.assertEqual(cf.limit_text(80, 1), "80% N-1")
        self.assertEqual(cf.limit_text(80.0, 2), "80% N-2")
        self.assertEqual(cf.limit_text(80, 0), "80%")                                          # a standalone host: no N-1
        self.assertEqual(cf.runway_to(None, "80% N-1"), "not growing (limit 80% N-1)")
        self.assertEqual(cf.runway_to(0, "80% N-1"), "80% N-1 reached")
        self.assertTrue(cf.runway_to(10, "80% N-1").startswith("10 days ("))
        self.assertTrue(cf.runway_to(10, "80% N-1").endswith(") to 80% N-1"))
        self.assertEqual(cf.runway_to(10 ** 9, "80%"), "10+ years to 80%")

    def test_standalone_host_runway_has_no_failover_term(self):
        data = json.loads(json.dumps(DATA))
        data["clusters"] = [dict(data["clusters"][1], name="ONE", standalone=True, hosts=data["clusters"][1]["hosts"][:1],
                                 history={"mem": line(100 * GB, 1 * GB), "cpu": line(10000, 0)})]
        data["datastores"] = []
        rep = cf.vm_capacity_report(data, OPTS)["report"]
        cell = rep["sections"][B + 1]["rows"][0][10]
        self.assertTrue(cell.endswith(") to 80% - Low"), cell)         # 154 days: within twice the 90 warn days (1.56.0)
        self.assertNotIn("N-", cell)


class Defaults(unittest.TestCase):
    def test_trend_is_fitted_on_90_days_and_the_history_read_covers_it(self):
        """Growth comes in batches: a 30-day line made one build-out the rate. The default is 90 days."""
        with open(os.path.join(HERE, "..", "roles", "vmware_vm", "defaults", "main.yml")) as f:
            got = {k: int(v) for k, v in re.findall(r"^(vm_capacity_(?:trend|history)_days):\s*(\d+)", f.read(), re.M)}
        self.assertEqual(got["vm_capacity_trend_days"], 90)
        self.assertGreaterEqual(got["vm_capacity_history_days"], got["vm_capacity_trend_days"])


class Report(unittest.TestCase):
    def test_report_and_items(self):
        r = cf.vm_capacity_report(DATA, OPTS)
        rep = r["report"]
        # 1.57.0: the first limit leads (the runway board's headline), then what is short as before
        self.assertEqual(rep["title"], "vSphere capacity planning: PROD memory at 80% N-1 now; 1 of 2 cluster(s) short on CPU or memory")
        self.assertEqual(rep["status"], "critical")
        titles = [s["title"] for s in rep["sections"]]
        self.assertEqual(titles[:B], ["Runway: the first limit per cluster", "Runway: datastores", "The numbers behind the lines"])
        self.assertEqual(titles[B:B + 5], ["Overall", "Clusters: CPU and memory", "Clusters: storage", "Datastores: runway", "Hosts per cluster"])
        comp = rep["sections"][B + 1]
        self.assertEqual(comp["rows"][0][0], "PROD")
        self.assertEqual(comp["cell_status"][0][5], "critical")                               # memory after host failure
        self.assertEqual(comp["rows"][1][11], "10 (memory)")
        self.assertEqual(comp["rows"][0][12], "1 now (memory 59% on average then); 5 within 12 months")
        self.assertEqual(rep["sections"][B + 0]["rows"][2][6], "1 recommended now, 5 within 12 months")
        # a cluster runway says what it runs to - the target with a host down, not full hosts
        self.assertTrue(rep["sections"][B + 0]["rows"][0][6].endswith(" to 80% N-1"), rep["sections"][B + 0]["rows"][0][6])   # CPU overall
        self.assertEqual(rep["sections"][B + 0]["rows"][1][6], "80% N-1 reached")                                          # memory overall
        self.assertEqual(comp["rows"][0][10], "80% N-1 reached")                                                       # PROD
        self.assertEqual(comp["rows"][1][10], "not growing (limit 80% N-1)")                                           # LAB
        self.assertIn("not to 100% of what is installed", rep["sections"][B + 0]["text"])
        self.assertIn("Cluster runways run to 80% N-1 - 80% of what is left with 1 host(s) down - not to full hosts", rep["footer"])
        # 1.56.0: ds_prod (102 days, Low) sorts before the ok ones
        self.assertEqual([i["job_limit"] for i in r["items"]], ["memory 80% N-1", "memory 80% N-1", "90% full", "", ""])
        self.assertEqual([i["id"] for i in r["items"]], ["P1", "P2", "P3", "P4", "P5"])
        self.assertEqual((r["items"][1]["kind"], r["items"][1]["name"], r["items"][1]["job_days"]), ("cluster", "PROD", 0))
        json.dumps(r["numbers"])                                                               # artifacts must be JSON
        ev = cf.vm_capacity_evidence(r, OPTS)
        self.assertIn("P2 cluster PROD", ev)
        self.assertIn("P3 datastore ds_prod", ev)
        self.assertNotIn("local1", ev)
        task = cf.vm_capacity_act_task(r["items"], OPTS)
        self.assertIn("estimate_days", task)
        self.assertIn("Do not write buy, purchase", task)
        self.assertIn("hosts recommended (average host here: 32 cores, 512.0 GB memory): 1 now, 5 within 12 months", ev)
        self.assertIn("memory growth +120.0 GB / month (fit R2 1.0 over 30.0 days), runway to 80% N-1 0 days; CPU runway to 80% N-1 680 days", ev)
        self.assertIn("(N-1: the cluster must still run everything - the memory and CPU runways run to that, not to full hosts)", ev)
        self.assertIn('with a host down ("80% N-1"), not to full hosts', task)
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertIn("esx1", cf.vm_capacity_names(DATA))

    def test_almost_flat_growth(self):
        """A few bytes a day: runways of millions of days (seen on a real vCenter) read 10+ years."""
        import copy
        d = copy.deepcopy(DATA)
        d["datastores"][1]["history"] = line(1 * TB, 1)
        d["clusters"][1]["history"]["mem"] = line(100 * GB, 1)
        r = cf.vm_capacity_report(d, OPTS)
        cells = [c for s in r["report"]["sections"] for row in s.get("rows") or [] for c in row]
        self.assertIn("10+ years", cells)
        json.dumps(r["numbers"])

    def test_math_vs_genai(self):
        self.assertEqual(cf.compare(100, 110), ("agree", "ok"))
        self.assertEqual(cf.compare(100, 20), ("GenAI 80 days sooner", "warning"))
        self.assertEqual(cf.compare(None, 40), ("only GenAI sees a limit", "warning"))
        self.assertEqual(cf.compare(40, None), ("only the math sees a limit", "warning"))
        self.assertEqual(cf.compare(None, None), ("agree: no limit in sight", "ok"))
        self.assertEqual(cf.compare(15933, None), ("agree: no limit in sight", "ok"))      # 40 years out: no limit
        self.assertEqual(cf.days_text(15933), "10+ years")
        self.assertEqual(cf.days_text(None), "not growing")
        r = cf.vm_capacity_report(DATA, OPTS)
        answer = ("BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Add a host to PROD now.", "items": [
            {"id": "P2", "estimate": "now", "estimate_days": 0, "recommendation": "add one 512 GB host", "reasoning": "117 % after a failure",
             "confidence": 90},
            {"id": "P3", "estimate": "in about 2 months", "estimate_days": "55", "recommendation": "grow ds_prod", "reasoning": "10 GB a day",
             "confidence": 65}]}) + "\nEND_ACT_ANALYSIS")
        parsed = va.act_analysis_parse(answer, r["items"])
        self.assertEqual(parsed["by_id"]["P3"]["estimate_days"], 55)
        self.assertEqual(parsed["by_id"]["P2"]["recommendation"], "add one 512 GB host")
        secs = cf.vm_capacity_act_section(r["items"], parsed, dict(OPTS, ran=True))
        self.assertEqual(secs[0]["lines"], ["Add a host to PROD now."])
        t = secs[1]
        p2 = [row for row in t["rows"] if row[0] == "P2"][0]
        p4 = [row for row in t["rows"] if row[0] == "P3"][0]
        self.assertEqual((p2[2], p2[3], p2[4]), ("0 days (memory 80% N-1)", "0 days", "agree"))
        self.assertEqual((p4[2], p4[3], p4[4]), ("102 days (90% full)", "55 days", "GenAI 47 days sooner"))
        no = cf.vm_capacity_act_section(r["items"], {}, {"ran": False, "reason": "no key"})
        self.assertEqual(no[0]["lines"], ["ACT did not run: no key"])


class TwoVcenters(unittest.TestCase):
    """Two vCenters, both with a cluster PROD in a datacenter DC1 (and moids that repeat): never merged."""
    def setUp(self):
        import copy
        a, b = copy.deepcopy(DATA), copy.deepcopy(DATA)
        for x in a["clusters"] + a["datastores"]:
            x["vcenter"] = "vc01.example.mil"
        for x in b["clusters"] + b["datastores"]:
            x["vcenter"] = "vc02.example.mil"
        b["clusters"] = b["clusters"][:1]                   # vc02: PROD only, and its own ds_prod
        b["datastores"] = b["datastores"][:1]
        b["datastores"][0]["capacity"] = 20 * TB             # bigger: tells the two ds_prod apart
        self.d = dict(DATA, vcenter="vc01.example.mil, vc02.example.mil",
                      vcenters=[{"name": "vc01.example.mil", "ok": True, "error": "", "note": ""},
                                {"name": "vc02.example.mil", "ok": True, "error": "", "note": ""}],
                      clusters=a["clusters"] + b["clusters"], datastores=a["datastores"] + b["datastores"])

    def test_numbers_not_merged(self):
        N = cf.capacity_numbers(self.d, OPTS)
        prods = [n for n in N["clusters"] if n["name"] == "PROD"]
        self.assertEqual(sorted(n["label"] for n in prods), ["PROD (vc01 / DC1)", "PROD (vc02 / DC1)"])
        caps = {n["vcenter"]: n["storage"]["capacity"] for n in prods}
        self.assertEqual(caps, {"vc01.example.mil": 10 * TB, "vc02.example.mil": 20 * TB})
        self.assertEqual([n["label"] for n in N["clusters"] if n["name"] == "LAB"], ["LAB"])     # unique: its name
        self.assertEqual(N["overall"]["clusters"], 3)

    def test_report_and_items(self):
        r = cf.vm_capacity_report(self.d, OPTS)
        self.assertIn("vCenters vc01.example.mil, vc02.example.mil", r["report"]["subtitle"])
        comp = r["report"]["sections"][B + 1]
        self.assertEqual(sorted(row[0] for row in comp["rows"]), ["LAB", "PROD (vc01 / DC1)", "PROD (vc02 / DC1)"])
        self.assertIn("vc02 / DC1", [row[1] for row in comp["rows"]])
        names = [i["name"] for i in r["items"]]
        self.assertEqual(len(names), len(set(names)))                                           # every ACT item can be told apart
        self.assertIn("ds_prod (vc02 / DC1)", names)
        ev = cf.vm_capacity_evidence(r, OPTS)
        self.assertIn("cluster PROD (vc02 / DC1) (datacenter vc02 / DC1", ev)
        self.assertIn("vc02", cf.vm_capacity_names(self.d))

    def test_one_vcenter_unchanged(self):
        r = cf.vm_capacity_report(DATA, OPTS)
        self.assertEqual(sorted(row[0] for row in r["report"]["sections"][B + 1]["rows"]), ["LAB", "PROD"])
        self.assertTrue(r["report"]["subtitle"].startswith("vCenter vc01 - read"))


class VcenterList(unittest.TestCase):
    def test_list_and_port(self):
        env = {"VMWARE_HOST": "vc01.example.mil", "VMWARE_PORT": "443"}
        self.assertEqual(site_vcenters.vcenter_list(["https://VC01.example.mil/", "vc02.example.mil", " ", "vc02.example.mil:8443"], env),
                         ["vc01.example.mil", "vc02.example.mil", "vc02.example.mil:8443"])
        self.assertEqual(site_vcenters.vcenter_list([], {}), [])
        self.assertEqual(site_vcenters.host_port("vc02:8443", env), ("vc02", 8443))
        self.assertEqual(site_vcenters.host_port("vc02", {"VMWARE_PORT": "9443"}), ("vc02", 9443))
        self.assertEqual(site_vcenters.host_port("fe80::1", env), ("fe80::1", 443))

    def test_refused_login_says_why_without_the_user_name(self):
        r = site_vcenters.refused
        self.assertIn("has no user name", r("vc01", {"VMWARE_USER": " "}))
        bare = r("vc01", {"VMWARE_USER": "svc-deploy"})
        self.assertIn("name@vsphere.local", bare)
        self.assertNotIn("svc-deploy", bare)
        for user in ("svc-deploy@vsphere.local", "VSPHERE.LOCAL\\svc-deploy"):
            msg = r("vc02", {"VMWARE_USER": user})
            self.assertTrue(msg.startswith("vCenter vc02 refused the login (the VMware vCenter credential): "))
            self.assertIn("single sign-on domain", msg)
            self.assertNotIn("svc-deploy", msg)

    def test_login_accepted_but_no_role_in_that_vcenter(self):
        class Fault:
            privilegeId = "System.View"
        msg = site_vcenters.no_rights("vc02", Fault())
        self.assertTrue(msg.startswith("vCenter vc02 accepted the login, but the account has no permission in that vCenter"))
        self.assertIn("lacks System.View", msg)
        self.assertIn("Global Permissions", msg)
        self.assertIn("lacks System.View", site_vcenters.no_rights("vc02"))


class ModuleParts(unittest.TestCase):
    def test_series_drops_missing(self):
        self.assertEqual(sc.series([3, 1, 2, 4], [30, 10, -1, 40], 1024), [[1, 10240.0], [3, 30720.0], [4, 40960.0]])

    def test_ha_policy(self):
        ns = types.SimpleNamespace
        off = ns(dasConfig=ns(enabled=False))
        self.assertEqual(sc.ha_policy(off)["enabled"], False)

        class ClusterFailoverLevelAdmissionControlPolicy(object):
            failoverLevel = 2
        cfg = ns(dasConfig=ns(enabled=True, admissionControlEnabled=True, admissionControlPolicy=ClusterFailoverLevelAdmissionControlPolicy()))
        self.assertEqual((sc.ha_policy(cfg)["kind"], sc.ha_policy(cfg)["hosts"]), ("hosts", 2))
        self.assertEqual(sc.ha_policy(None)["enabled"], False)


# ---- 1.57.0: the runway board -------------------------------------------------------------------------------------
def bent(start, rates, end=NOW):
    """A daily history over sum(days): [(days, per_day), ...] one after the other, ending at end."""
    total = sum(d for d, _ in rates)
    out, v, i = [], float(start), 0
    for d, per in rates:
        for _ in range(d):
            out.append([end - (total - i) * DAY, v])
            v += per
            i += 1
    out.append([end, v])
    return out


def board_cluster(name, hist_mem, n=4, mem_gb=512):
    used = hist_mem[-1][1] if hist_mem else 300 * GB
    hs = [host("%s-esx%d" % (name.lower(), i), mem_gb, 0) for i in range(n)]
    for h in hs:
        h["mem_used_bytes"] = int(used / n)
    return {"name": name, "datacenter": "DC1", "standalone": False, "ha": {"enabled": True, "kind": "hosts", "hosts": 1}, "hosts": hs,
            "vms_on": 40, "vms_off": 0, "vcpus_on": 80, "mem_configured_on": 1000 * GB, "added": 0, "removed": 0,
            "history": {"mem": hist_mem, "cpu": bent(10000, [(120, 0)])}}


def board_data():
    """SOON grows faster in the last month (its runway sooner than a month ago), LATER slower (further out), NEW has 20
    days of history (no forecast a month ago); ds_step steps up 9 points on one day."""
    return {"vcenter": "vc01", "read_at": READ, "history_days": 120, "events_days": 30, "notes": [],
            "clusters": [board_cluster("SOON", bent(600 * GB, [(90, 1 * GB), (30, 6 * GB)])),
                         board_cluster("LATER", bent(500 * GB, [(90, 5 * GB), (30, 0.5 * GB)])),
                         board_cluster("NEW", bent(700 * GB, [(20, 2 * GB)]))],
            "datastores": [{"name": "ds_step", "datacenter": "DC1", "clusters": ["SOON"], "hosts": 4, "shared": True, "pod": "", "type": "VMFS",
                            "accessible": True, "capacity": 10 * TB, "free": 10 * TB - int(6.9 * TB), "uncommitted": 0,
                            "history": [[t, v + (0.9 * TB if t >= NOW - 40 * DAY else 0)] for t, v in bent(5 * TB, [(120, 0.002 * TB)])]}]
            + [{"name": "ds_%02d" % i, "datacenter": "DC1", "clusters": ["LATER"], "hosts": 4, "shared": True, "pod": "", "type": "NFS",
                "accessible": True, "capacity": 10 * TB, "free": 6 * TB, "uncommitted": 0, "history": bent(3.5 * TB, [(120, 0.004 * TB)])}
               for i in range(12)]}


BOPTS = dict(OPTS, trend_days=90, top_datastores=10, horizon_months=12)


class Board(unittest.TestCase):
    def setUp(self):
        self.r = cf.vm_capacity_report(board_data(), BOPTS)
        self.rows = {r["name"]: r for s in self.r["web"]["sections"] if s.get("board") for r in s["board"]["rows"]}

    def test_moved_sooner_further_and_no_forecast_a_month_ago(self):
        mem = lambda name: [c for c in self.rows[name]["cells"] if c["res"] == "Memory"][0]   # noqa: E731
        self.assertEqual(mem("SOON")["moved"]["kind"], "sooner")
        self.assertIn("sooner - was", mem("SOON")["moved"]["text"])
        self.assertEqual(mem("LATER")["moved"]["kind"], "later")
        self.assertIn("further out - was", mem("LATER")["moved"]["text"])
        self.assertEqual(mem("NEW")["moved"], {"kind": "none", "days": None, "text": "no forecast a month ago", "was": ""})
        # the same line on the samples up to 30 days ago: its runway from the value then, dated from then
        then = cf.then_forecast(board_data()["clusters"][0]["history"]["mem"], 1000 * GB, BOPTS, NOW)
        self.assertAlmostEqual(then["trend"]["slope"], 1 * GB, delta=0.01 * GB)
        self.assertEqual(then["date"], NOW - 30 * DAY + then["days"] * DAY)
        self.assertIsNone(cf.then_forecast(bent(1, [(20, 1)]), 100, BOPTS, NOW))

    def test_band_edges(self):
        self.assertEqual([cf.band(d, 90, 30) for d in (0, 30, 31, 90, 91, 180, 181, None)],
                         ["critical", "critical", "warning", "warning", "low", "low", "ok", "ok"])
        self.assertEqual([cf.band(d, 90, 30) for d in (30, 90)], [cf.runway_status(d, 90, 30) for d in (30, 90)])  # as the tables
        self.assertEqual(cf.band_status("low"), "low")            # 1.56.0: the Low status
        self.assertEqual(cf.band_text("low", 90, 30), "Low (3-6 months)")
        self.assertEqual(cf.band_text("critical", 30.4, 10), "Critical (0.3 months or less)")
        self.assertEqual(cf.band_text("critical", 90, 30.4), "Critical (1 month or less)")

    def test_a_step_change(self):
        st = self.rows["ds_step"]["cells"][0]
        step = cf.step_in(st["series"])
        self.assertIsNotNone(step)
        self.assertAlmostEqual(step[1], 9.0, delta=0.2)
        self.assertIn("a step of +9 points on", self.rows["ds_step"]["since"])
        self.assertIsNone(cf.step_in([[i * DAY, 50 + 0.1 * i] for i in range(60)]))      # steady growth: no step

    def test_tiles_headline_and_the_email(self):
        rep, web = self.r["report"], self.r["web"]
        self.assertEqual([t["label"] for t in rep["summary"][:4]], ["First to fill", "Moved closer", "Biggest move", "Moved further out"])
        self.assertEqual(rep["summary"][4]["label"], "Clusters")                 # the summary as before, after them
        self.assertTrue(all(t["tip"] and "\n" not in t["tip"] for t in rep["summary"][:4]))   # one plain line each
        first = rep["summary"][0]
        self.assertTrue(rep["title"].startswith("vSphere capacity planning: %s " % first["value"]), rep["title"])
        self.assertEqual(rep["summary"][1]["value"], 1)                         # SOON moved closer
        self.assertEqual(rep["summary"][3]["value"], 1)                         # LATER further out
        # the email: tables only (no board, no svg); the website: the board, and the same numbers table
        self.assertFalse(any("board" in s for s in rep["sections"]))
        self.assertNotIn("<svg", json.dumps(rep))
        self.assertEqual([s["title"] for s in rep["sections"][:3]], [s["title"] for s in web["sections"][:3]])
        self.assertEqual(rep["sections"][2], web["sections"][2])
        self.assertEqual(rep["sections"][2]["columns"], ["Cluster or datastore", "Resource", "90 days ago", "60 days ago", "30 days ago", "Now"])
        soon = [r for r in rep["sections"][2]["rows"] if r[:2] == ["SOON", "Memory"]][0]
        self.assertEqual(soon[2:], ["%.0f%%" % (100.0 * (600 + 30) / 2048), "%.0f%%" % (100.0 * (600 + 60) / 2048),
                                    "%.0f%%" % (100.0 * (600 + 90) / 2048), "%.0f%%" % (100.0 * (600 + 90 + 180) / 2048)])
        # nothing existing removed: every section the report had is still there, ACT's place before "Hosts per cluster"
        titles = [s["title"] for s in rep["sections"]]
        for t in ("Overall", "Clusters: CPU and memory", "Clusters: storage", "Datastores: runway", "Hosts per cluster"):
            self.assertIn(t, titles)
        self.assertEqual(titles[self.r["act_at"]], "Hosts per cluster")
        self.assertEqual(web["sections"][self.r["act_at"]]["title"], "Hosts per cluster")

    def test_top_datastores_on_the_page_all_in_the_workbook(self):
        rep = self.r["report"]
        ds = [s for s in rep["sections"] if s["title"] == "Runway: datastores"][0]
        self.assertEqual(len(ds["rows"]), 10)
        self.assertIn("The first 10 of 13", ds["text"])
        sheets = cf.vm_capacity_sheets([{"name": "Overall", "rows": [1]}, {"name": "Runway: datastores", "rows": ds["rows"]}], self.r["sheet"])
        self.assertEqual(len(sheets[1]["rows"]), 13)
        self.assertEqual(sheets[0], {"name": "Overall", "rows": [1]})

    def test_without_history(self):
        """vcsim: no daily statistics - no line, no forecast, the report as before"""
        d = json.loads(json.dumps(DATA))
        for c in d["clusters"]:
            c["history"] = {}
        for x in d["datastores"]:
            x["history"] = []
        r = cf.vm_capacity_report(d, OPTS)
        self.assertTrue(r["report"]["title"].startswith("vSphere capacity planning: "))
        b = [s for s in r["web"]["sections"] if s.get("board")][0]["board"]
        self.assertTrue(all(len(c["series"]) == 0 for row in b["rows"] for c in row["cells"]))
        self.assertTrue(all(c["moved"]["kind"] == "none" for row in b["rows"] for c in row["cells"]))


if __name__ == "__main__":
    unittest.main()
