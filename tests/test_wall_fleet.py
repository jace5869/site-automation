#!/usr/bin/env python3
"""1.136.0: the wall's Ops monitor area (roles/site_portal_stack/files/wall_fleet.py, static/wall-fleet.js, the node share in
static/wall-share.js): the grouping (SolarWinds groups, a custom property, the configured list), the informational vCenter inventory
(unique identities, dated notes and unmatched VMs), stale never green, the since-tracker, 3,000 nodes, the payload,
the reader (SWQL SELECTs only, a refused login, a column Orion has not) against the fake SWIS, the share text and HTML (escaped),
and the page in each style (headless Chrome). Test data only (tests/wall/fleet_demo.py)."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
for p in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), HERE, os.path.join(HERE, "wall"),
          os.path.join(HERE, "netapp"), os.path.join(HERE, "site_portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

import wall_fleet as wf  # noqa: E402
import wall_model as wm  # noqa: E402
import fleet_demo as fd  # noqa: E402
import site_swis  # noqa: E402

OPTS = wf.opts_of({})[0]


def sw_row(nid, name, status=1, dns=None, ip="192.0.2.10", vendor="VMware Inc.", model="VMware Virtual Platform", loss=0, rt=3, unmanaged=False, cp=None):
    r = {"NodeID": nid, "Caption": name, "DNS": dns if dns is not None else name.lower() + ".corp.example.mil", "IPAddress": ip, "Status": status,
         "StatusDescription": "Node status is %s." % {1: "Up", 2: "Down", 3: "Warning"}.get(status, "Unknown"), "Vendor": vendor, "MachineType": model,
         "ResponseTime": -1 if status == 2 else rt, "PercentLoss": 100 if status == 2 else loss, "UnManaged": unmanaged,
         "LastSync": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 40))}
    if cp is not None:
        r["cp_group"] = cp
    return r


def vm(name, power="poweredOn", hb="green", ip="", guest="", host="esx01.corp.example.mil", tools="guestToolsRunning", template=False, vcls=False, mid=None):
    return {"id": mid or "vm-" + name, "name": name, "power": power, "heartbeat": hb, "tools": tools, "tools_version": "guestToolsCurrent", "tools_seen": True,
            "template": template, "vcls": vcls, "ip": ip, "guest": guest, "host": host}


def view(vms, now, age=20, state="ok", name="vc01.corp.example.mil"):
    return {"sources": {"vcenter:" + name: {"kind": "vcenter", "name": name, "state": state, "at": now - age if age is not None else None,
                                            "snap": {"hosts": [], "clusters": [], "datastores": [], "vms": vms}}}, "history": {}}


NOGROUPS = {"list": [], "members": {}, "cut": False}       # an Orion with no groups set up: read, whole, empty


def snap(rows, groups=NOGROUPS, now=None, age=30, state="ok", cut=False):
    at = (now or time.time()) - age
    return {"state": state, "why": "", "at": at, "tried": at, "fail_since": None, "next_try": at + 60, "nodes": rows, "cut": cut, "notes": [],
            "groups": groups, "groups_at": at, "groups_why": "", "reads": 1}


def by_name(fm):
    """SolarWinds' rows by name (a vCenter-only VM of the same name is not one of them)"""
    return dict((r[1], r) for r in fm["nodes"] if "v" not in r[12])


class Settings(unittest.TestCase):
    def test_defaults_and_refusals(self):
        o, bad = wf.opts_of({})
        self.assertEqual((o["seconds"], o["group_by"], o["groups"], o["vm_match"]), (60, "groups", [], ["*VMware*"]))
        o, bad = wf.opts_of({"monitor_seconds": 5, "monitor_group_by": "custom:ServiceGroup", "monitor_groups": ["AD", 3, ""]})
        self.assertEqual((o["seconds"], o["group_by"], o["groups"]), (60, "custom:ServiceGroup", []))
        for gb in ("custom:Service Group", "custom:x;DROP", "custom:", "custom:1abc", "Location"):   # SWQL text: letters, digits, _ only
            o, bad = wf.opts_of({"monitor_group_by": gb})
            self.assertEqual(o["group_by"], "groups", gb)
            self.assertTrue(bad, gb)
        self.assertEqual(wf.opts_of({"monitor_seconds": 99999})[0]["seconds"], 3600)

    def test_late_and_stale_stretch_with_the_interval(self):
        self.assertEqual(wf.limits(60), (180, 600))
        self.assertEqual(wf.limits(600), (1230, 2400))


class Grouping(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def test_solarwinds_groups_direct_members_and_two_groups(self):
        rows = [sw_row(1, "SITEADIRDCV01"), sw_row(2, "SITEACERTV01"), sw_row(3, "SITEAAPPV001"), sw_row(4, "SITEADIRDCV02", status=2)]
        groups = {"list": [[10, "Certificate Services", 1], [11, "Active Directory", 2], [12, "Empty", 0], [13, "Tier 0", 1]],
                  "members": {"1": [11, 13], "2": [10], "4": [11]}, "cut": False}
        fm = wf.model(snap(rows, groups, self.now), None, OPTS, self.now)
        names = [g[0] for g in fm["groups"]]
        self.assertEqual(names, ["Active Directory", "Certificate Services", "Tier 0", wf.NO_GROUP])     # A to Z, empty left out, no vCenter: no vCenter-only group
        n = by_name(fm)
        self.assertEqual(n["SITEADIRDCV01"][0], 0)                     # the first group in order: where its square is
        self.assertEqual(n["SITEADIRDCV01"][14], [2])                  # also in Tier 0 (the card says so)
        self.assertEqual(n["SITEAAPPV001"][0], 3)                      # in no group: Workloads (no group)
        self.assertEqual(fm["groups"][0][2], "d")                      # SolarWinds' own rollup, as a code
        cs, ok = wf.conds(fm, self.now)
        ids = sorted(c["id"] for c in cs)
        self.assertEqual(ids, ["fleet:down:Active Directory"])
        self.assertIn("fleet:down:Tier 0", ok)                        # Tier 0's member is up: its condition may clear
        self.assertEqual(sum(fm["counts"].values()), 4)               # a node in two groups counted once

    def test_the_configured_list_limits_and_orders(self):
        rows = [sw_row(1, "A1"), sw_row(2, "B1"), sw_row(3, "C1")]
        groups = {"list": [[1, "Alpha", 1], [2, "Beta", 1], [3, "Gamma", 1]], "members": {"1": [1], "2": [2], "3": [3]}, "cut": False}
        o = wf.opts_of({"monitor_groups": ["gamma", "ALPHA", "Nope"]})[0]
        fm = wf.model(snap(rows, groups, self.now), None, o, self.now)
        self.assertEqual([g[0] for g in fm["groups"]], ["Gamma", "Alpha", wf.NO_GROUP])
        self.assertEqual(by_name(fm)["B1"][0], 2)                       # Beta not listed: under Workloads (no group)

    def test_a_custom_property(self):
        rows = [sw_row(1, "A1", cp="Active Directory"), sw_row(2, "A2", cp="active directory"), sw_row(3, "B1", cp=""), sw_row(4, "C1", cp=None)]
        o = wf.opts_of({"monitor_group_by": "custom:ServiceGroup"})[0]
        fm = wf.model(snap(rows, now=self.now), None, o, self.now)
        self.assertEqual([g[0] for g in fm["groups"]], ["Active Directory", wf.NO_GROUP])     # any case: one group, its first spelling
        self.assertEqual(fm["groups"][0][1], "c")
        self.assertEqual([r[0] for r in fm["nodes"]], [0, 0, 1, 1])


class Compare(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def fm(self, rows, vms, age=20, state="ok", o=OPTS):
        return wf.model(snap(rows, now=self.now), view(vms, self.now, age, state), o, self.now)

    def test_vcenter_only_and_not_found(self):
        rows = [sw_row(1, "SITEAOLDV01"), sw_row(2, "SITEASWACC001", vendor="Cisco", model="Catalyst 9300"), sw_row(3, "SITEAAPPV001")]
        vms = [vm("SITEAAPPV001.corp.example.mil"), vm("SITEAVMX01"), vm("SITEAVMX02", hb="red"), vm("SITEAVMX03", power="poweredOff", hb=None),
               vm("SITEAVMX04", hb=None, tools="guestToolsNotRunning"), vm("TEMPLATE01", template=True), vm("vCLS-1", vcls=True)]
        fm = self.fm(rows, vms)
        n = by_name(fm)
        self.assertEqual(fm["cmp"]["not_in_vc"], 1)                    # a VM by Vendor / MachineType; the switch is no VM
        self.assertIn("n", n["SITEAOLDV01"][12])
        self.assertNotIn("n", n["SITEASWACC001"][12])
        self.assertEqual(fm["groups"][-1][0], wf.VC_ONLY)
        vco = dict((r[1], r[18]) for r in fm["nodes"] if "v" in r[12])
        self.assertEqual(vco, dict.fromkeys(("SITEAVMX01", "SITEAVMX02", "SITEAVMX03", "SITEAVMX04"), "info"))
        self.assertNotIn("TEMPLATE01", vco)
        self.assertEqual(fm["cmp"]["vc_only"], 4)
        self.assertEqual([c for c in wf.conds(fm, self.now)[0] if "vCenter only" in c["id"]], [])   # the VMware area's, not here

    def test_not_found_is_not_counted_before_every_vcenter_was_read(self):
        rows = [sw_row(1, "SITEAOLDV01")]
        fm = wf.model(snap(rows, now=self.now), {"sources": {"vcenter:a": {"kind": "vcenter", "name": "a", "state": "ok", "at": self.now - 10, "snap": {"vms": []}},
                                                                "vcenter:b": {"kind": "vcenter", "name": "b", "state": "auth", "at": None, "snap": None}}}, OPTS, self.now)
        self.assertEqual(fm["cmp"]["not_in_vc"], 0)
        self.assertFalse(fm["cmp"]["vc_read"])

    def test_exact_names_any_case_and_two_of_a_name(self):
        rows = [sw_row(1, "SITEADB01", ip="192.0.2.21"), sw_row(2, "siteadb02.corp.example.mil", dns="", ip="192.0.2.22"),
                sw_row(3, "SITEADB03", ip="192.0.2.23"), sw_row(4, "192.0.2.40", dns="", ip="192.0.2.40")]
        vms = [vm("siteadb01.corp.example.mil", ip="192.0.2.21"), vm("SITEADB02"),
               vm("SITEADB03.corp.example.mil", mid="vm-a"), vm("siteadb03.corp.example.mil.", mid="vm-b"),
               vm("SITEAAPP77", guest="192.0.2.40")]
        fm = self.fm(rows, vms)
        n = by_name(fm)
        self.assertEqual(n["SITEADB01"][9], "1")                        # matched: powered on, heartbeat OK
        self.assertEqual(n["siteadb02.corp.example.mil"][9], "")
        self.assertEqual(n["SITEADB03"][9], "")                         # two VMs, the IP settles nothing: not compared, said
        self.assertIn("a", n["SITEADB03"][12])
        self.assertEqual(fm["cmp"]["ambiguous"], 1)
        self.assertFalse([r for r in fm["nodes"] if "v" in r[12] and r[1].lower().startswith("siteadb03")])    # neither is "vCenter only"
        self.assertEqual(n["192.0.2.40"][9], "")                         # an IP caption is no name
        rows[2]["IPAddress"] = "192.0.2.98"
        vms[2]["ip"] = "192.0.2.97"
        vms[3]["ip"] = "192.0.2.98"
        n = by_name(self.fm(rows, vms))
        self.assertEqual((n["SITEADB03"][9], n["SITEADB03"][12]), ("", "a"))     # both VMs still share its FQDN


class Review1(unittest.TestCase):
    """review round 1 (Codex): recovery needs known members, another domain or IP is another machine, an old Orion poll,
    the groups' completeness, every membership, absence only from a whole read, the since clock, the back-off clock"""

    def setUp(self):
        self.now = time.time()

    def test_unknown_never_clears_a_down_group(self):
        groups = {"list": [[1, "AD", 1]], "members": {"1": [1], "2": [1]}, "cut": False}
        fm = wf.model(snap([sw_row(1, "DC1", status=2), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
        self.assertEqual([c["id"] for c in wf.conds(fm, self.now)[0]], ["fleet:down:AD"])
        for st, um in ((0, False), (9, True), (1, True)):                 # unknown blocks recovery, unmanaged plus up permits it
            fm = wf.model(snap([sw_row(1, "DC1", status=st, unmanaged=um), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
            cs, ok = wf.conds(fm, self.now)
            self.assertEqual((cs, "fleet:down:AD" in ok), ([], um), (st, um))
        fm = wf.model(snap([sw_row(1, "DC1"), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
        self.assertIn("fleet:down:AD", wf.conds(fm, self.now)[1])         # both known up: it clears

    def test_another_domain_or_ip_is_another_machine(self):
        rows = [sw_row(1, "APP", dns="app.prod.example.mil", ip="192.0.2.10", status=2)]
        for v in (vm("app.test.example.mil", power="poweredOff", hb=None), vm("APP", power="poweredOff", hb=None, ip="192.0.2.99")):
            fm = wf.model(snap(rows, now=self.now), view([v], self.now), OPTS, self.now)
            n = by_name(fm)["APP"]
            self.assertEqual(n[3], "d", v["name"])                       # never greyed by another machine's power state
            self.assertIn("n", n[12])
            self.assertEqual([r[1] for r in fm["nodes"] if "v" in r[12]], [v["name"]])     # that VM stays "vCenter only"
        fm = wf.model(snap(rows, now=self.now), view([vm("app.prod.example.mil", power="poweredOff", hb=None)], self.now), OPTS, self.now)
        self.assertEqual(by_name(fm)["APP"][9], "o")

    def test_an_old_orion_poll_is_not_current(self):
        r = sw_row(1, "A1")
        r["LastSync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - 3600))
        groups = {"list": [[1, "G", 1]], "members": {"1": [1]}, "cut": False}
        fm = wf.model(snap([r], groups, self.now, age=0), None, OPTS, self.now)
        row = fm["nodes"][0]
        self.assertIn("o", row[12])
        self.assertIn("last polled it 60 min", row[8])
        self.assertNotIn("fleet:down:G", wf.conds(fm, self.now)[1])

    def test_groups_not_whole_are_not_whole(self):
        rows = [sw_row(1, "A1")]
        fm = wf.model(dict(snap(rows, None, now=self.now), groups_why="the groups were not read: HTTP 400"), None, OPTS, self.now)
        self.assertFalse(fm["complete"])
        self.assertIn("groups were not read", " ".join(fm["incomplete"]))
        fm = wf.model(snap(rows, {"list": [[1, "G", 1]], "members": {"1": [1]}, "cut": True}, self.now), None, OPTS, self.now)
        self.assertFalse(fm["complete"])
        s2 = snap([sw_row(1, "A1", cp="AD")], now=self.now)
        s2["notes"] = ["custom property ServiceGroup: not read (HTTP 400)"]
        self.assertFalse(wf.model(s2, None, wf.opts_of({"monitor_group_by": "custom:ServiceGroup"})[0], self.now)["complete"])

    def test_every_membership_counts(self):
        groups = {"list": [[1, "Alpha", 1], [2, "Beta", 1]], "members": {"1": [1, 2], "2": [2]}, "cut": False}
        fm = wf.model(snap([sw_row(1, "SHARED", status=2), sw_row(2, "B1")], groups, self.now), None, OPTS, self.now)
        self.assertEqual(sorted(c["id"] for c in wf.conds(fm, self.now)[0]), ["fleet:down:Alpha", "fleet:down:Beta"])
        self.assertEqual(sorted(w[0] for w in wf.summary(fm)["worst"]), ["Alpha", "Beta"])

    def test_absence_only_from_a_whole_current_read(self):
        vms = [vm("SITEAVMX01")]
        for sn in (snap([sw_row(1, "A1")], now=self.now, cut=True), dict(snap([], now=self.now), nodes=None, state="auth", at=None),
                   snap([sw_row(1, "A1")], now=self.now, age=3600)):
            fm = wf.model(sn, view(vms, self.now), wf.opts_of({"monitor_group_by": "custom:X"})[0], self.now)
            self.assertIsNone(fm["cmp"]["vc_only"])
            self.assertFalse([r for r in fm["nodes"] if "v" in r[12]])
        two = view([vm("x")], self.now)
        two["sources"]["vcenter:b"] = {"kind": "vcenter", "name": "b", "state": "ok", "at": self.now - 900, "snap": {"vms": []}}
        fm = wf.model(snap([sw_row(1, "OLDV")], now=self.now), two, OPTS, self.now)
        self.assertFalse(fm["cmp"]["vc_read"])                              # one vCenter old: "not found" not counted
        self.assertEqual(fm["cmp"]["not_in_vc"], 0)

    def test_since_by_each_sources_own_read(self):
        s = wf.Since()
        out = s.update({"sw:1": ("d", 1900.0), "vc:a:1": ("u", 1950.0)}, 1900.0)
        out = s.update({"sw:1": ("d", 2000.0), "vc:a:1": ("d", 2100.0)}, 2000.0)
        self.assertEqual(out["vc:a:1"], (2100.0, True))
        s.update({"sw:1": ("d", 2060.0)}, 2060.0, whole=False)             # a cut read forgets nothing
        self.assertIn("vc:a:1", s.d)

    def test_backoff_counts_from_the_failure(self):
        t = [1000.0]

        class Slow(FakeClient):
            def query(self2, *a, **k):
                t[0] += 600                                                 # the refusal after 10 min
                raise site_swis.SwisError("refused", "auth")
        r = wf.Reader(lambda: Slow([]), wf.opts_of({"monitor_group_by": "custom:X"})[0], clock=lambda: t[0], auth_retry=900)
        try:
            r.read_once()
        except site_swis.SwisError as e:
            r._failed(e, r.clock())
        self.assertEqual(r.snapshot()["next_try"], 1600.0 + 900)

    def test_groups_tried_every_15_minutes_even_when_never_read(self):
        c = FakeClient([sw_row(1, "A1")], None, refuse=("Orion.Container",))
        c.groups = {"list": [], "members": []}
        t = [1000.0]
        r = wf.Reader(lambda: c, OPTS, clock=lambda: t[0])
        r.read_once()
        t[0] += 60
        r.read_once()
        self.assertEqual(sum(1 for q, p in c.q if "Metadata.Entity" in q), 1)


class Review1Neon(unittest.TestCase):
    """review round 1 (Neon): two nodes for one VM, generic guest names, an empty read, the since gap after a restart, a
    refused column asked once, counts of SolarWinds' nodes only, unread template flags"""

    def setUp(self):
        self.now = time.time()

    def test_two_nodes_one_vm(self):
        rows = [sw_row(1, "APP1", dns="", ip="192.0.2.5", status=2), sw_row(2, "app1", dns="", ip="192.0.2.6", status=2)]
        fm = wf.model(snap(rows, now=self.now), view([vm("APP1", power="poweredOff", hb=None)], self.now), OPTS, self.now)
        self.assertEqual([r[3] for r in fm["nodes"] if "v" not in r[12]], ["d", "d"])      # neither greyed by a VM that is one of them
        fm = wf.model(snap(rows, now=self.now), view([vm("APP1", power="poweredOff", hb=None, ip="192.0.2.6")], self.now), OPTS, self.now)
        self.assertEqual([r[9] for r in fm["nodes"] if "v" not in r[12]], ["", "o"])

    def test_generic_guest_names_never_match(self):
        rows = [sw_row(1, "localhost", dns="", ip="")]
        fm = wf.model(snap(rows, now=self.now), view([vm("SITEAVMX09", guest="localhost.localdomain")], self.now), OPTS, self.now)
        self.assertEqual(fm["cmp"]["matched"], 0)

    def test_an_empty_read_is_not_whole(self):
        fm = wf.model(snap([], now=self.now), None, OPTS, self.now)
        self.assertFalse(fm["complete"])

    def test_since_after_a_long_gap_is_not_exact(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        s = wf.Since(os.path.join(d, "s.json"))
        s.update({"sw:1": "u"}, 1000.0)
        s2 = wf.Since(os.path.join(d, "s.json"))                            # the portal was off for an hour
        self.assertEqual(s2.update({"sw:1": "d"}, 4600.0)["sw:1"], (4600.0, False))

    def test_a_refused_column_is_asked_once(self):
        c = FakeClient([sw_row(1, "A1")], NOGROUPS_Q, refuse=("LastSync",))
        r = wf.Reader(lambda: c, OPTS)
        r.read_once()
        n1 = sum(1 for q, p in c.q if "LastSync" in q)
        r.read_once()
        self.assertEqual(sum(1 for q, p in c.q if "LastSync" in q), n1)

    def test_counts_are_solarwinds_nodes(self):
        fm = wf.model(snap([sw_row(1, "A1")], now=self.now), view([vm("SITEAVMX01", hb="red")], self.now), OPTS, self.now)
        self.assertEqual((fm["counts"]["u"], fm["counts"]["d"]), (1, 0))
        self.assertEqual(fm["cmp"]["vc_only"], 1)

    def test_unread_template_flag(self):
        fm = wf.model(snap([sw_row(1, "A1")], now=self.now), view([vm("T1", power="poweredOff", hb=None, template=None)], self.now), OPTS, self.now)
        self.assertEqual(fm["cmp"]["vc_only"], 0)


NOGROUPS_Q = {"list": [], "members": []}


class Review2(unittest.TestCase):
    """review round 2 (Codex): one pair of names for an identity, the IP before the tiers, unknown and odd rows not whole, a
    missing poll time, cached members after a failed refresh, each source's history kept, a group attempt kept, many VMs of a name"""

    def setUp(self):
        self.now = time.time()

    def test_identity_from_one_pair_of_names(self):
        rows = [sw_row(1, "app.prod.example.mil", dns="alias.shared.example.mil", ip="", status=2)]
        v = vm("app.test.example.mil", guest="other.shared.example.mil", power="poweredOff", hb=None)
        fm = wf.model(snap(rows, now=self.now), view([v], self.now), OPTS, self.now)
        self.assertEqual(by_name(fm)["app.prod.example.mil"][3], "d")       # "app" from one alias, "shared" from another: no match

    def test_ip_conflict_never_hides_a_short_name_vm(self):
        rows = [sw_row(1, "db1.corp.example.mil", dns="", ip="192.0.2.7", status=2)]
        vms = [vm("db1.corp.example.mil", ip="192.0.2.99", power="poweredOff", hb=None), vm("DB1", ip="192.0.2.7", power="poweredOff", hb=None)]
        fm = wf.model(snap(rows, now=self.now), view(vms, self.now), OPTS, self.now)
        self.assertEqual(by_name(fm)["db1.corp.example.mil"][9], "")  # name and IP identify different VMs

    def test_unknown_and_odd_rows_are_not_whole(self):
        fm = wf.model(snap([sw_row(1, "A1", status=0)], now=self.now), None, OPTS, self.now)
        self.assertFalse(fm["complete"])
        fm = wf.model(snap([sw_row(1, "A1"), {"Caption": "no id"}], now=self.now), None, OPTS, self.now)
        self.assertFalse(fm["complete"])

    def test_a_missing_poll_time_is_not_current(self):
        r = sw_row(1, "A1")
        r["LastSync"] = None
        fm = wf.model(snap([r], now=self.now), None, OPTS, self.now)
        self.assertIn("o", fm["nodes"][0][12])
        self.assertEqual(fm["counts"]["u"], 0)
        s2 = snap([dict(r)], now=self.now)
        s2["notes"] = ["the last poll time (LastSync): not read (HTTP 400)"]
        self.assertIn("o", wf.model(s2, None, OPTS, self.now)["nodes"][0][12])

    def test_cached_members_after_a_failed_refresh(self):
        g = {"list": [[1, "G", 1]], "members": {"1": [1]}, "cut": False}
        fm = wf.model(dict(snap([sw_row(1, "A1")], g, self.now), groups_why="the groups were not read: HTTP 400 - the last read's groups are shown"), None, OPTS, self.now)
        self.assertFalse(fm["complete"])
        self.assertNotIn("fleet:down:G", wf.conds(fm, self.now)[1])

    def test_each_sources_history_kept(self):
        s = wf.Since()
        s.update({"sw:1": ("d", 100.0), "vc:a:1": ("d", 100.0)}, 100.0)
        s.update({"sw:1": ("d", 160.0)}, 160.0, whole=("sw:",))             # a whole SolarWinds read, vCenter not read
        self.assertIn("vc:a:1", s.d)
        s.update({"sw:2": ("u", 220.0)}, 220.0, whole=("sw:",))
        self.assertNotIn("sw:1", s.d)

    def test_a_group_attempt_is_kept_when_the_node_read_fails(self):
        class Fails(FakeClient):
            def query(self2, swql, *a, **k):
                if "FROM Orion.Nodes" in swql:
                    raise site_swis.SwisError("no answer in time", "timeout")
                return FakeClient.query(self2, swql, *a, **k)
        c = Fails([], {"list": [], "members": []})
        r = wf.Reader(lambda: c, OPTS, clock=lambda: 1000.0)
        with self.assertRaises(site_swis.SwisError):
            r.read_once()
        self.assertEqual(r.snapshot()["groups_tried"], 1000.0)

    def test_many_vms_of_one_name_are_fast(self):
        rows = [sw_row(i, "app.corp.example.mil", dns="", ip="") for i in range(1, 3001)]
        vms = [vm("app.corp.example.mil", mid="vm-%d" % i) for i in range(3000)]
        t = time.time()
        fm = wf.model(snap(rows, now=self.now), view(vms, self.now), OPTS, self.now)
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(fm["cmp"]["ambiguous"], 3000)


class Review2Neon(unittest.TestCase):
    """review round 2 (Neon): an old poll is not whole even for a powered-off VM, the group attempt kept with its outcome, a
    crowded name keeps its VMs out of "vCenter only", each source's own gap"""

    def setUp(self):
        self.now = time.time()

    def test_old_polls_are_not_whole(self):
        r = sw_row(1, "A1")
        r["LastSync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - 7200))
        fm = wf.model(snap([r], now=self.now, age=0), None, OPTS, self.now)
        self.assertFalse(fm["complete"])
        r2 = dict(r, Status=2, Caption="VMOFF", DNS="")
        fm = wf.model(snap([r2], now=self.now, age=0), view([vm("VMOFF", power="poweredOff", hb=None)], self.now), OPTS, self.now)
        self.assertEqual((fm["nodes"][0][3], "o" in fm["nodes"][0][12], fm["complete"]), ("d", True, False))

    def test_group_attempt_with_its_outcome(self):
        class Fails(FakeClient):
            def query(self2, swql, *a, **k):
                if "FROM Orion.Nodes" in swql:
                    raise site_swis.SwisError("no answer in time", "timeout")
                return FakeClient.query(self2, swql, *a, **k)
        c = Fails([], {"list": [{"ContainerID": 1, "Name": "G", "Status": 1}], "members": []})
        r = wf.Reader(lambda: c, OPTS, clock=lambda: 1000.0)
        with self.assertRaises(site_swis.SwisError):
            r.read_once()
        sn = r.snapshot()
        self.assertEqual((sn["groups_tried"], bool(sn["groups"])), (1000.0, True))     # the groups it read are kept with the attempt

    def test_a_crowded_name(self):
        rows = [sw_row(1, "app.corp.example.mil", dns="", ip="")]
        vms = [vm("app.corp.example.mil", mid="vm-%d" % i) for i in range(80)] + [vm("OTHER")]
        fm = wf.model(snap(rows, now=self.now), view(vms, self.now), OPTS, self.now)
        self.assertEqual([r[1] for r in fm["nodes"] if "v" in r[12]], ["OTHER"])

    def test_each_sources_own_gap(self):
        s = wf.Since()
        s.update({"sw:1": ("u", 0.0), "vc:a:1": ("u", 0.0)}, 0.0)
        for t in range(60, 4000, 60):
            s.update({"sw:1": ("u", float(t))}, float(t), whole=("sw:",))
        self.assertEqual(s.update({"vc:a:1": ("d", 4000.0)}, 4000.0, whole=())["vc:a:1"], (4000.0, False))   # vCenter silent an hour


class StaleNeverGreen(unittest.TestCase):
    """the area through the wall's own model (wall_model.build / summary): green only when SolarWinds is current, the read whole
    and nothing down"""

    def area(self, fm, now):
        m = wm.build(None, None, None, {}, wm.Tracker(), now, fleet=fm)
        s = wm.summary(m, now)
        return [a for a in s["areas"] if a["id"] == "monitor"][0], m

    def test_rules(self):
        now = time.time()
        up = [sw_row(1, "A1"), sw_row(2, "A2")]
        a, m = self.area(wf.model(snap(up, now=now, age=30), None, OPTS, now), now)
        self.assertEqual(a["state"], "ok")
        self.assertEqual(m["feeds"]["sw:fleet"]["fresh"], "ok")
        a, m = self.area(wf.model(snap(up, now=now, age=900), None, OPTS, now), now)
        self.assertEqual(a["state"], "stale")                           # 15 min old: never green
        a, m = self.area(wf.model(snap(up, now=now, age=240), None, OPTS, now), now)
        self.assertEqual(a["state"], "stale")                           # LATE: never green either
        a, m = self.area(wf.model(snap(up, now=now, age=30, cut=True), None, OPTS, now), now)
        self.assertEqual(a["state"], "stale")                           # cut at max_nodes: not whole
        down = [sw_row(1, "A1", status=2), sw_row(2, "A2")]
        a, m = self.area(wf.model(snap(down, now=now, age=30), None, OPTS, now), now)
        self.assertEqual((a["state"], a["crit"]), ("crit", 1))
        a, m = self.area(wf.model(snap(down, now=now, age=900), None, OPTS, now), now)
        self.assertEqual(a["state"], "stale")                           # down as last known: grey, said, never green
        st = dict(snap(up, now=now), state="auth", nodes=None, at=None)
        a, m = self.area(wf.model(st, None, OPTS, now), now)
        self.assertEqual(a["state"], "stale")
        m = wm.build(None, None, None, {}, wm.Tracker(), now, fleet=None)
        self.assertFalse(any(x["id"] == "monitor" for x in m["areas"]))  # off: not on the wall at all

    def test_a_longer_interval_stretches_late(self):
        now = time.time()
        o = wf.opts_of({"monitor_seconds": 600, "monitor_poll_stale_minutes": 30})[0]
        row = dict(sw_row(1, "A1"), LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 900)))
        a, m = self.area(wf.model(snap([row], now=now, age=900), None, o, now), now)
        self.assertEqual(a["state"], "ok")                              # read every 10 min: 15 min old is inside its own LATE

    def test_on_but_not_readable_says_why(self):
        now = time.time()
        m = wm.build(None, None, None, {}, wm.Tracker(), now, fleet=None, fleet_why="no SolarWinds login")
        a = [x for x in m["areas"] if x["id"] == "monitor"][0]
        self.assertEqual((a["on"], a["why"]), (False, "no SolarWinds login"))


class SinceTracker(unittest.TestCase):
    def test_exact_after_an_up_read_and_kept_across_restarts(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "since.json")
        s = wf.Since(path)
        out = s.update({"sw:1": "d", "sw:2": "u"}, 1000)
        self.assertEqual(out["sw:1"], (1000.0, False))                 # down at the first read: "since then or earlier"
        out = s.update({"sw:1": "d", "sw:2": "d"}, 1060)
        self.assertEqual(out["sw:1"], (1000.0, False))
        self.assertEqual(out["sw:2"], (1060.0, True))                  # seen up, then down: exact
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        s2 = wf.Since(path)                                            # a restart
        self.assertEqual(s2.update({"sw:2": "d"}, 1120)["sw:2"], (1060.0, True))
        with open(path, "w") as fh:
            fh.write("not json")
        self.assertEqual(wf.Since(path).d, {})                          # an odd file: starts again, never a crash

    def test_since_in_the_rows(self):
        now = time.time()
        s = wf.Since()
        wf.model(snap([sw_row(1, "A1")], now=now, age=60), None, OPTS, now - 30, since=s)
        fm = wf.model(snap([sw_row(1, "A1", status=2)], now=now, age=0), None, OPTS, now, since=s)
        r = fm["nodes"][0]
        self.assertEqual((r[3], r[7]), ("d", 1))
        self.assertAlmostEqual(r[6], now, delta=2)


class Scale(unittest.TestCase):
    def test_three_thousand_nodes(self):
        fx = fd.fixture(4)
        now = time.time()
        sn, v = fd.FakeReader(fx).snapshot(), fd.view(now, fx)
        self.assertGreaterEqual(len(sn["nodes"]), 3000)
        t = time.time()
        fm = wf.model(sn, v, OPTS, now, since=wf.Since())
        dt = time.time() - t
        body = json.dumps(fm, separators=(",", ":"))
        self.assertLess(dt, 2.0, "the model of 3,000 nodes took %.2f s" % dt)
        self.assertLess(len(body), 450 * 1024, "the answer is %d bytes" % len(body))
        t = time.time()
        m = wm.build(v, None, None, {}, wm.Tracker(), now, fleet=fm)
        self.assertLess(time.time() - t, 1.0)
        self.assertLess(len(json.dumps(m["fleet"])), 4096)              # the wall's own answer carries only the summary


class Payload(unittest.TestCase):
    def test_compact_rows(self):
        fx = fd.fixture()
        now = time.time()
        fm = wf.model(fd.FakeReader(fx).snapshot(), fd.view(now, fx), OPTS, now, since=wf.Since())
        self.assertEqual(len(fm["row"]), 20)
        codes = set(r[3] for r in fm["nodes"])
        self.assertTrue(codes <= set("uwdpmx"), codes)
        for r in fm["nodes"]:
            self.assertEqual(len(r), 20)
            self.assertIn(r[9], list(wf.VMW) + [""])
            self.assertTrue(-1 <= r[10] < len(fm["hosts"]))
            if r[3] == "u":
                self.assertEqual((r[8], r[16]), ("", ""))                # nothing said of an up node but its numbers
        self.assertEqual(fm["counts"]["d"], 13)                          # SolarWinds' nodes (the vCenter-only VM is the VMware area's)
        xss = [r for r in fm["nodes"] if "onerror" in r[1]]
        self.assertEqual(len(xss), 1)                                  # data as it came: the page and the share escape it


class FakeClient(object):
    """site_swis.Client's two calls from canned answers; every query kept"""

    def __init__(self, rows, groups=None, refuse=(), auth=False):
        self.rows, self.groups, self.refuse, self.auth, self.q = rows, groups, refuse, auth, []
        self.url = "https://swis.example.invalid:17774"

    def query(self, swql, params=None):
        self.q.append((swql, params))
        if self.auth:
            raise site_swis.SwisError("SolarWinds refused the login (HTTP 401)", "auth")
        if any(x in swql for x in self.refuse):
            raise site_swis.SwisError("SolarWinds could not run this query (HTTP 400)", "query")
        if "Metadata.Entity" in swql:
            return [{"FullName": n} for n in params["names"] if self.groups is not None]
        if "Orion.ContainerMembers" in swql:
            members = self.groups["members"]
            if "WITH ROWS" in swql:
                start, end = map(int, re.search(r"WITH ROWS (\d+) TO (\d+)", swql).groups())
                return members[start - 1:end]
            if "MemberPrimaryID > @member" in swql:
                cursor = (params["container"], params["member"])
                members = [m for m in members if (m["ContainerID"], m["MemberPrimaryID"]) > cursor]
            return members[:int(swql.split()[2])]
        if "Orion.Container" in swql:
            return self.groups["list"]
        top = re.match(r"SELECT TOP (\d+) ", swql)
        if top:
            limit = int(top.group(1))
            return [r for r in self.rows if r["NodeID"] > (params or {}).get("last", -1)][:limit]
        start, end = re.search(r"WITH ROWS (\d+) TO (\d+)$", swql).groups()
        return self.rows[int(start)-1:int(end)]

    def paged(self, swql, order, params=None, page=1000, cap=None):
        return site_swis.Client.paged(self, swql, order, params, page, cap)


class Reader(unittest.TestCase):
    def test_selects_named_columns_and_groups(self):
        c = FakeClient([sw_row(1, "A1")], {"list": [{"ContainerID": 5, "Name": "AD", "Status": 1}],
                                           "members": [{"ContainerID": 5, "MemberPrimaryID": 1, "MemberEntityType": "Orion.Nodes"},
                                                       {"ContainerID": 5, "MemberPrimaryID": 1, "MemberEntityType": "Orion.Groups"}]})
        r = wf.Reader(lambda: c, OPTS)
        sn = r.read_once()
        self.assertEqual(sn["state"], "ok")
        self.assertEqual(sn["groups"]["members"], {"1": [5]})          # only node members, each group once
        for q, p in c.q:
            self.assertTrue(re.match(r"^SELECT ", q), q)
            self.assertNotIn("*", q)
            self.assertNotRegex(q, r"Community|LicenseKey")
        self.assertTrue(any("MemberEntityType IN @types" in q and p == {"types": ["Orion.Nodes"]} for q, p in c.q))

    def test_a_column_orion_has_not(self):
        c = FakeClient([sw_row(1, "A1", cp="AD")], None, refuse=("LastSync",))
        o = wf.opts_of({"monitor_group_by": "custom:ServiceGroup"})[0]
        sn = wf.Reader(lambda: c, o).read_once()
        self.assertEqual(sn["state"], "ok")
        self.assertTrue(any("LastSync" in n for n in sn["notes"]), sn["notes"])
        self.assertTrue(any("cp_group" in q for q, p in c.q))
        with self.assertRaises(ValueError):
            wf.read_nodes(c, OPTS, "x; DROP")                           # never into the SWQL text

    def test_a_refused_login_waits(self):
        lines = []
        now = [1000.0]
        r = wf.Reader(lambda: FakeClient([], auth=True), OPTS, log=lines.append, clock=lambda: now[0], auth_retry=900)
        try:
            r.read_once()
        except site_swis.SwisError as e:
            r._failed(e, now[0])
        sn = r.snapshot()
        self.assertEqual(sn["state"], "auth")
        self.assertEqual(sn["next_try"], 1900.0)                        # no retry loop on a refused login
        self.assertEqual(len(lines), 1)
        self.assertNotIn("Basic ", json.dumps(lines))                   # never the login itself

    def test_groups_not_read_keep_the_nodes(self):
        c = FakeClient([sw_row(1, "A1")], None, refuse=("Orion.Container",))
        c.groups = {"list": [], "members": []}
        sn = wf.Reader(lambda: c, OPTS).read_once()
        self.assertEqual(sn["state"], "ok")
        self.assertTrue(sn["groups_why"].startswith("the groups were not read"))
        fm = wf.model(sn, None, OPTS, time.time())
        self.assertFalse(fm["complete"])                                # the services need their members: not whole, said (review 1)
        self.assertTrue(any("groups were not read" in n for n in fm["notes"]))


class LiveSwql(unittest.TestCase):
    """the reader against the fake SWIS (tests/netmap/fake_swis.py, HTTPS) with the fleet's fixture"""

    def test_read_through_the_fake(self):
        from test_netmap import Fake, PW
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        fx = fd.fixture(1, "svc_swis_ro", PW)
        with open(os.path.join(tmp, "fx.json"), "w") as fh:
            json.dump(fx, fh)
        f = Fake(tmp)
        self.addCleanup(f.stop)
        for o in (OPTS, wf.opts_of({"monitor_group_by": "custom:ServiceGroup"})[0]):
            sn = wf.Reader(f.client, o).read_once()
            self.assertEqual(sn["state"], "ok", sn["why"])
            self.assertEqual(len(sn["nodes"]), len(fx["nodes"]))
            now = time.time()
            fm = wf.model(sn, fd.view(now, fx), o, now)
            self.assertEqual(fm["counts"]["d"], 13)                          # SolarWinds' nodes (the vCenter-only VM is the VMware area's)
            self.assertIn("Active Directory", [g[0] for g in fm["groups"]])
        before = sum(1 for r in sn["nodes"] if r["Status"] == 2)
        f.set_state({"down": [2002]})                                    # SITEADIRDCV02 goes down live
        sn = wf.Reader(f.client, OPTS).read_once()
        self.assertEqual(sum(1 for r in sn["nodes"] if r["Status"] == 2), before + 1)
        lines = f.lines()
        self.assertTrue(lines)
        self.assertFalse([x for x in lines if not x.startswith("POST /SolarWinds/InformationService/v3/Json/Query ")])
        self.assertFalse([x for x in lines if "SECRET" in x])
        bad = wf.Reader(lambda: f.client(password="wrong"), OPTS)
        with self.assertRaises(site_swis.SwisError) as e:
            bad.read_once()
        self.assertEqual(e.exception.kind, "auth")


# Run the production board in a VM with an in-memory DOM and a controllable wall clock.
# Attach the scenario inside the closure so the shipped board needs no test-only API.
GATE_JS = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const [dir, input, scenario] = process.argv.slice(1);
const data = JSON.parse(input);
let clock = data.now * 1000, dateOffset = 0;
function el(tag, cls, children) {
  return {tag, cls, attrs: arguments[3], insertBefore(x, before) {this.children.splice(this.children.indexOf(before), 0, x);}, focus() { env.document.activeElement = this; },
    querySelectorAll(tag) { return this.children.filter(x => x && x.querySelectorAll)
      .flatMap(x => (x.tag === tag ? [x] : []).concat(x.querySelectorAll(tag))); },
    children: (children || []).filter(x => x != null),
    get firstChild() { return this.children[0]; }, get lastChild() { return this.children.at(-1); },
    appendChild(x) { this.children.push(x); }, removeChild() { this.children.shift(); },
    classList: {add(){}, remove(){}, toggle(){}}, listeners: {}, addEventListener(type, fn) {this.listeners[type] = fn;}};
}
const api = {S: {area: "overview"}, h: el, NOW: () => clock, W: () => ({}), feed: () => null, unreach: () => false,
  dur: String, styleName: () => "", zoneName: () => "UTC", hm: String, hms: String, ago: String, word: (s, w) => w,
  nowList: () => [], src: "/api/wall"};
const env = {window: {}, document: {getElementById: () => null}, location: {origin: "https://portal.example.mil"},
  Blob, AbortController, setTimeout, clearTimeout, setInterval(){}, requestAnimationFrame(){}, assert, api, data, el,
  performance: {now: () => clock}, shiftDate(s) {dateOffset += s*1000;}, advance(s) { clock += s * 1000; }, Date: class extends Date { static now() { return clock + dateOffset; } }};
vm.createContext(env);
function script(name, setup) {
  const code = fs.readFileSync(dir + "/" + name, "utf8");
  vm.runInContext(code.replace(/\}\)\(\);\s*$/, () => setup + "\n})();"), env);
}
script("wall-share.js", "");
env.realPanel = env.window.SAWallShare.nodes.panel;
env.window.SAWallShare.nodes.panel = (rows, ctx) => {
  env.panel = {rows, ctx, text: env.window.SAWallShare.nodes.text(rows, ctx)};
  const panel = el("div", "", [el("button"), el("button")]);
  panel.__focus = panel.firstChild;
  return panel;
};
if (data.wall || data.actions) {
  env.URLSearchParams = URLSearchParams;
  env.window.fetch = () => {};
  env.document.getElementById = () => ({getAttribute() {}, setAttribute() {}, querySelector() {return null;}});
  env.window.scrollTo = () => {};
  const partial = (name, end, setup) => vm.runInContext(fs.readFileSync(dir + "/" + name, "utf8").split(end)[0] + setup + "})();", env);
  if (data.wall) {
    const source = fs.readFileSync(dir + "/wall-page.js", "utf8");
    const loader = source.slice(source.indexOf("  function load(first)"), source.indexOf("  // the test harness only (data-test)"));
    const detail = source.slice(source.indexOf("  function openDetail("), source.indexOf("  function closeDetail("));
    const share = source.slice(source.indexOf("  function openShare("), source.indexOf("  var API ="));
    partial("wall-page.js", "  function overallState", loader + detail + share + `
    SRC = api.src; window.wallModel = () => W;
    window.refreshShare = () => {
      const id = "group";
      BY[id] = {id,area:"monitor",kind:"fleetdown",sev:"crit",end:NOW()};
      openShare(id);
      assert.equal(window.sharedCondition.sev, "cleared");
      BY[id] = {...BY[id], end:null};
      openDetail("c:"+id,true);
    };
    const card = {querySelector:()=>true};
    function verify() {return "";}
    window.SAWallShare.open = (_, condition) => {window.sharedCondition = condition;};
    function render() {} LOADED = true; window.loadWall = load;
    W = adapt(JSON.parse(JSON.stringify(data.wall))); api.areaState = areaState; api.areaCounts = areaCounts; window.freshSignature = freshSig;`);
  }
  if (data.actions) partial("portal-looks.js", '  sec.addEventListener("click"', `
    window.mapBounds = () => {
      const ids = ["storage","vmware","vdi","ad","tickets","monitor"];
      D.areas = ids.map(id=>({id,name:id,state:["storage","vmware","monitor"].includes(id)?"crit":"off",crit:3}));
      h = el; s = (tag,attrs,kids)=>({tag,attrs,kids});
      return statusMap().children[1].kids.filter(k=>k.tag==="a").map(a=>{
        const xy = a.kids[0].attrs.d.match(/-?[0-9.]+/g).map(Number);
        const xs = xy.filter((_,i)=>!(i%2)), ys = xy.filter((_,i)=>i%2);
        const labels = a.kids.filter(k=>k.tag==="text");
        return {left:Math.min(...xs),right:Math.max(...xs),top:Math.min(...ys),bottom:Math.max(...ys,labels[1].attrs.y),
                center:[labels[0].attrs.x,labels[0].attrs.y-5]};
      });
    };
    render = () => {}; window.loadActions = load; D0 = {areas:[data.actions], now:data.now}; D = {now:data.now}; window.actions = () => {aged(); return D.areas[0];};`);
  env.document.getElementById = () => null;
}
script("wall-styles.js", "A = api; window.tile = fleetInfo; window.stage = st => stageOf({id:'monitor', st, name:'Ops monitor'});");
script("wall-fleet.js", `
  A = api; F = adapt(data); B = {};
  P = {ov: el(), svc: el(), logo: el(), small: el(), site: el(), clock: el("", "", [{}, {}])};
  // The surrounding board panels do not participate in the dialog's lifecycle.
  srcs = pills = banner = notUp = compare = spotlight = footer = () => {};
  async function wallReply(body) {
    globalThis.fetch = async()=>({ok:true,json:async()=>JSON.parse(JSON.stringify(body))});
    await window.loadWall(false);
  }
  async function deliverAll() {
    globalThis.fetch = async()=>({status:200,json:async()=>data}); await load(false);
    await wallReply(data.wall);
    globalThis.fetch = async()=>({ok:true,json:async()=>({now:data.now,areas:[data.actions]})}); await window.loadActions();
  }
  const card = () => JSON.stringify(P.ov.children);
  ` + scenario);
"""


class Gate(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.groups = {"list": [[1, "Service", 1]], "members": {"1": [1], "2": [1]}}
        self.rows = [sw_row(1, "APP"), sw_row(2, "DB")]

    def js(self, fm, scenario):
        if not shutil.which("node"):
            self.skipTest("no node")
        result = subprocess.run(["node", "-e", GATE_JS, os.path.join(FILES, "static"), json.dumps(fm), scenario],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def live(self, sn):
        from types import SimpleNamespace
        from site_portal import WallLive
        obj = WallLive.__new__(WallLive)
        obj.fleet = SimpleNamespace(snapshot=lambda: sn)
        obj.cfg = SimpleNamespace(wall_fleet_opts=OPTS)
        obj.fleet_since = obj.fleet_key = obj.fleet_model = None
        return obj

    def test_gate_1_missing_group_member(self):
        fm = wf.model(snap(self.rows[:1], self.groups, self.now), None, OPTS, self.now)
        self.assertIn("1 group members not in the node read", fm["incomplete"])
        self.assertFalse(wf.conds(fm, self.now)[1])
        self.js(fm, 'const c = gState(F.groups[0]); assert.equal(c.n, 2); assert.equal(c.st, "stale");')

    def test_gate_2_unreadable_lastsync(self):
        for status in (1, 2, 3):
            for value, notes in ((None, []), ("invalid", []), (self.rows[0]["LastSync"], ["LastSync refused"])):
                sn = snap([dict(self.rows[0], Status=status, LastSync=value)], now=self.now)
                fm = wf.model(dict(sn, notes=notes), None, OPTS, self.now)
                self.assertIn("o", fm["nodes"][0][12])
                self.assertFalse(fm["complete"])
                self.assertEqual(fm["counts"]["u"], 0)
                self.assertFalse(wf.conds(fm, self.now)[1])

    def test_gate_3_poll_deadline_on_wall_clock(self):
        poll = self.now - 890
        row = dict(self.rows[0], LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(poll)))
        sn = snap([row], now=self.now, age=0)
        live = self.live(sn)
        fm = live.fleet_of(None, self.now)
        self.assertEqual((fm["nodes"][0][15], fm["row"][15], fm["poll_stale"]), (poll, "polled_at", 900))
        self.js(fm, 'assert.equal(shown(F.nodes[0]), "up"); advance(11); assert.equal(shown(F.nodes[0]), "unk");')
        self.assertFalse(live.fleet_of(None, self.now + 30)["complete"])
        for offset in (120, 121):
            row["LastSync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now + offset))
            fm = wf.model(sn, None, OPTS, self.now)
            self.assertEqual("o" in fm["nodes"][0][12], offset > 120)
        row["LastSync"] = time.strftime("%Y-%m-%dT%H:%M:%S.500Z", time.gmtime(self.now + 120))
        self.assertFalse(wf.model(sn, None, OPTS, self.now)["complete"])

    def test_gate_5_refresh_open_card_and_bulk(self):
        fm = wf.model(snap(self.rows, self.groups, self.now), None, OPTS, self.now)
        self.js(fm, """
          openCard([F.by["1"]]); assert.equal(panel.rows[0].sev, "ok");
          data.feed.state = "error"; data.nodes.forEach(r => {r[18] = "unk"; r[19] = null;}); F = adapt(data); refresh();
          assert.equal(panel.rows[0].sev, "stale"); assert(panel.ctx.stale);
          assert(panel.text.startsWith("[UNKNOWN]")); assert(card().includes("tag stale"));
          openCard(F.nodes, "bulk");
          data.nodes.shift(); data.nodes[0][3] = "w"; data.nodes[0][18] = "warn"; data.nodes[0][19] = data.now + 30; data.feed.state = "ok"; F = adapt(data); refresh();
          assert.equal(OV.kind, "bulk"); assert.equal(panel.rows.length, 1);
          assert.equal(panel.rows[0].name, "DB"); assert.equal(panel.rows[0].sev, "warn");
          data.nodes = []; F = adapt(data); refresh(); assert.equal(OV, null); assert.equal(card(), "[]");
        """)

    def test_gate_6_status_words_and_unmonitored_service(self):
        for sn in (snap(self.rows, self.groups, self.now, state="error"), snap(self.rows, self.groups, self.now, cut=True)):
            fm = wf.model(sn, None, OPTS, self.now)
            area, _ = StaleNeverGreen().area(fm, self.now)
            self.assertEqual((area["state"], area["big"], area["unit"]), ("stale", "–", "no current data"))
            self.js(fm, """
              openCard([F.nodes[0]]); assert(panel.text.startsWith("[UNKNOWN]")); assert(panel.ctx.stale);
              assert(card().includes('"tag stale","children":["UNKNOWN"]'));
              assert(card().includes('"cls":"stale","children":["unknown'));
            """)
        rows = [dict(r, UnManaged=True) for r in self.rows]
        fm = wf.model(snap(rows, self.groups, self.now), view([vm("DB", power="poweredOff")], self.now), OPTS, self.now)
        self.assertFalse(wf.conds(fm, self.now)[1])
        only_off = wf.model(snap(rows[1:], now=self.now), view([vm("DB", power="poweredOff")], self.now), OPTS, self.now)
        self.assertFalse(wf.conds(only_off, self.now)[1])
        self.assertEqual(StaleNeverGreen().area(fm, self.now)[0]["state"], "off")
        self.js(fm, """
          services(); assert.equal(gState(F.groups[0]).st, "off");
          assert(JSON.stringify(P.svc).includes("no monitored nodes"));
          assert(JSON.stringify(window.stage("ok")).includes("No service group with a node down or warning."));
          assert(JSON.stringify(window.stage("stale")).includes("No current data."));
        """)

    def test_gate_7_group_read_invalidates_cache(self):
        sn = snap(self.rows, self.groups, self.now)
        live = self.live(sn)
        fm = live.fleet_of(None, self.now)
        self.assertTrue(fm["complete"])
        for key, value in (("groups_why", "groups refused"), ("groups_tried", self.now), ("groups_at", self.now)):
            sn[key] = value
            updated = live.fleet_of(None, self.now)
            self.assertIsNot(updated, fm, key)
            self.assertFalse(updated["complete"])
            fm = updated

    def test_gate_8_crowded_exact_name(self):
        import tracemalloc
        name = "app.corp.example.mil"
        rows = [sw_row(i, name, dns=name, ip="") for i in range(10000)]
        sn = snap(rows, now=self.now)
        vc = view([vm(name, mid=str(i)) for i in range(10000)], self.now)
        # Fail cheaply on implementations that keep all candidates per node.
        vms, _ = wf.vcenter_vms(view([vm(name) for _ in range(64)], self.now), self.now, wf.LATE_S)
        pairs = wf.match([{"name": name, "dns": name, "ip": ""}], vms)
        self.assertIsNone(pairs[0][0])
        tracemalloc.start()
        try:
            start = time.monotonic()
            fm = wf.model(sn, vc, OPTS, self.now)
            elapsed = time.monotonic() - start
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual((fm["cmp"]["ambiguous"], fm["cmp"]["vc_only"]), (10000, 0))
        self.assertLess(elapsed, 10)
        self.assertLess(peak, 150_000_000)


class Gate2(unittest.TestCase):
    setUp = Gate.setUp
    live = Gate.live
    js = Gate.js

    def test_gate2_2_impossible_clock(self):
        for stamp in ("2025-02-29T10:00:00", "2024-04-31T10:00:00", "2024-12-00T10:00:00",
                      "2024-13-01T10:00:00", "2024-02-29T24:00:00", "2024-02-29T09:60:00",
                      "2024-02-29T09:00:60", "2024-02-29T09:00:61"):
            with self.subTest(stamp=stamp):
                self.assertIsNone(wf.epoch(stamp + "Z"))
        self.assertEqual(wf.epoch("2024-02-29T00:30:00.5+01:00"), wf.epoch("2024-02-28T23:30:00.5Z"))

    def test_gate2_3_server_poll_deadline(self):
        now = self.now - self.now % 30
        for status in (1, 2, 3):
            row = dict(self.rows[0], Status=status, LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 895)))
            live = self.live(snap([row], now=now, age=0))
            fm = live.fleet_of(None, now)
            self.assertIs(fm, live.fleet_of(None, now + 4.99))
            expired = live.fleet_of(None, now + 5)
            self.assertIn("o", expired["nodes"][0][12])
            self.assertFalse(wf.conds(expired, now + 5)[1])
            self.assertEqual(fm["next_expiry"], now + 5)
            self.assertIs(expired, live.fleet_of(None, now + 31))

    def test_gate2_4_actions_earliest_deadline(self):
        for poll_age, sw_age, vc_age, expected in ((40, 0, 179, 180), (898, 0, 20, 2), (177, 177, 20, 3)):
            rows = [dict(self.rows[0], LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - poll_age))),
                    dict(self.rows[1], LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - sw_age)))]
            vc = view([vm("DB", power="poweredOff"), vm("OTHER")], self.now, age=vc_age)
            fm = wf.model(snap(rows, now=self.now, age=sw_age), vc, OPTS, self.now)
            area, _ = StaleNeverGreen().area(fm, self.now)
            self.assertEqual((area["state"], area["ok_until"]), ("ok", self.now + expected))

        for age in (179, 181):
            vc = view([vm("OTHER")], self.now, age=age)
            fm = wf.model(snap(self.rows, now=self.now), vc, OPTS, self.now)
            area, _ = StaleNeverGreen().area(fm, self.now)
            self.assertEqual((area["state"], area["ok_until"]), ("ok", self.now + 150))

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_gate2_6_actions_aged_count(self):
        script = r"""
        const fs = require("fs"), assert = require("assert");
        const document = {getElementById: () => ({getAttribute() {}})}, window = {fetch() {}};
        const code = fs.readFileSync(process.argv[1], "utf8").split('  sec.addEventListener("click"')[0];
        eval(code + `
          const t = Date.now() / 1000;
          for (const [state, age] of [["ok", 2], ["ok", 700], ["warn", 2]]) {
            D0 = {now: t-age, areas: [{id:"monitor", valid_until:t-1, state, big:"2/2", unit:"nodes up", ok_until:t-1, live:[["warn", t-1]]}]};
            D = JSON.parse(JSON.stringify(D0)); receipt = performance.now() - age * 1000; aged();
            assert.deepEqual([D.areas[0].state, D.areas[0].big, D.areas[0].unit], ["stale","–","no current data"]);
          }
        })();`);
        """
        result = subprocess.run(["node", "-e", script, os.path.join(FILES, "static", "portal-looks.js")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate2_a_crowded_alias(self):
        rows = [sw_row(1, "hot.corp.example.mil", dns="db.prod.corp.example.mil")]
        name = "db.test.corp.example.mil"
        vms = [vm("hot.corp.example.mil", mid=str(i)) for i in range(51)] + [vm(name, hb="red", ip="192.0.2.20")]
        fm = wf.model(snap(rows, now=self.now), view(vms, self.now), OPTS, self.now)
        self.assertEqual(fm["cmp"]["ambiguous"], 1)
        self.assertEqual([(r[1], r[3]) for r in fm["nodes"] if "v" in r[12]], [(name, wf.UNKNOWN)])

    def test_gate2_b_placeholder_limits(self):
        self.groups["members"] = {str(i): [1] for i in range(1, 305)}
        for cap in (500, 20, 2):
            fm = wf.model(snap(self.rows, self.groups, self.now), None, dict(OPTS, max_nodes=cap), self.now)
            self.assertEqual(len(fm["nodes"]), min(cap, 202))
            self.assertIn("302 group members not in the node read", fm["incomplete"])
            self.assertEqual(fm["groups"][0][3]["total"], 304)
            self.assertEqual(fm["verdict"]["total"], 304)
            self.assertFalse(wf.conds(fm, self.now)[1])

        fm = wf.model(snap(self.rows, now=self.now), view([vm("EXTRA")], self.now), dict(OPTS, max_nodes=2), self.now)
        self.assertEqual((len(fm["nodes"]), fm["cmp"]["vc_only"], fm["complete"]), (2, 1, True))

    def test_gate2_c_card_refresh_and_focus(self):
        fm = wf.model(snap(self.rows + [sw_row(3, "OTHER")], now=self.now), None, OPTS, self.now)
        self.js(fm, """
          for (const kind of ["bulk", "card"]) {
            openCard(kind === "bulk" ? [F.by["1"], F.by["2"]] : [F.by["1"]], kind);
            const initial = panel, button = P.ov.querySelectorAll("button")[2];
            button.focus();
            data.nodes[2][3] = "w"; F = adapt(data); refresh();
            assert.strictEqual(panel, initial); assert.strictEqual(document.activeElement, button);
            data.nodes[0][8] += " changed"; F = adapt(data); refresh();
            assert.notStrictEqual(panel, initial);
            assert.strictEqual(document.activeElement, P.ov.querySelectorAll("button")[2]);
          }
        """)



class Gate3(unittest.TestCase):
    setUp = Gate.setUp
    js = Gate.js
    live = Gate.live

    def model(self, vc=None):
        return wf.model(snap(self.rows, self.groups, self.now), vc, OPTS, self.now)

    def expiring(self):
        self.rows[0]["LastSync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now - 895))

    def test_gate3_1_overview_poll_deadline(self):
        self.expiring()
        fm = self.model()
        _, fm["wall"] = StaleNeverGreen().area(fm, self.now)
        self.js(fm, """
          api.W = () => data.wall;
          assert.equal(window.tile().big, "2/2");
          const before = window.freshSignature(); advance(6);
          assert.notEqual(before, window.freshSignature());
          const tile = window.tile();
          assert.deepEqual([tile.st, tile.big, tile.unit], ["stale", "–", "no current data"]);
          assert(!JSON.stringify(window.stage(tile.st)).includes("No service group with a node down or warning."));
        """)

    def test_gate3_2_actions_expires_with_live_down(self):
        self.expiring()
        self.rows[1]["Status"] = 2
        fm = self.model()
        area, wall = StaleNeverGreen().area(fm, self.now)
        self.assertEqual((area["state"], area["big"]), ("crit", "1/2"))
        self.assertEqual(area["live"], [["crit", self.now + 5]])
        expired = next(a for a in wm.summary(wall, self.now + 6)["areas"] if a["id"] == "monitor")
        self.assertEqual((expired["state"], expired["big"], expired["unit"]), ("stale", "–", "no current data"))
        fm["actions"] = area
        self.js(fm, """
          assert.equal(window.actions().big, "1/2"); advance(6);
          const a = window.actions();
          assert.deepEqual([a.state, a.big, a.unit, a.crit], ["stale", "–", "no current data", 0]);
        """)

    def test_gate3_3_open_comparison_refresh(self):
        fm = self.model(view([vm("EXTRA")], self.now))
        fm["next"] = self.model(view([vm("EXTRA", hb="red")], self.now))
        self.js(fm, """
          B = el(); P.cv = el(); document.addEventListener = () => {}; wire();
          const cmp = {getAttribute: () => "v"};
          B.listeners.click({target: {closest: q => q === "[data-cmp]" ? cmp : null}});

          assert(card().includes('"INFO"')); F = adapt(data.next); refresh();
          assert(card().includes('"INFO"')); assert(!card().includes('"UP"'));
          P.more = {closest: () => null}; B.listeners.click({target: P.more});
          F = adapt(data); refresh();
          assert(!card().includes("EXTRA"));
        """)

    def test_gate3_4_interrupted_body_and_retry(self):
        from threading import Lock
        live = self.live(snap(self.rows, self.groups, self.now))
        live.cfg.tz_name, live.cfg.wall_title, live.cfg.wall_enclave = "UTC", "", ""
        live.collector, live.lock = None, Lock()
        from site_portal import make_handler
        from types import SimpleNamespace
        from test_netapp_live import cfg_for
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfg_for(tmp, PORTAL_NETAPP="0", PORTAL_AD="0")
            live.viewed = lambda: None
            handler = make_handler(cfg, SimpleNamespace(log=None), tmp, live)
            request = handler.__new__(handler)
            request.path, request.headers = "/api/wall/fleet", {"If-None-Match": "*"}
            request._who = lambda api: "viewer"
            request._send = lambda code, body, ctype, headers=None: (code, body, headers)
            for _ in range(2):
                code, body, headers = request.do_GET()
                self.assertEqual((code, headers), (200, None))
                self.assertEqual(len(json.loads(body)["nodes"]), 2)
        self.js(self.model(), """
          (async () => {
            let response, headers;
            globalThis.fetch = async (_, opts) => { headers = opts.headers; return response; };
            response = {status:200, ok:true, headers:{get:()=>"v2"}, json:async()=>{throw Error("short body");}};
            await load(false); assert.equal(shown(F.nodes[0]), "unk");
            response = {status:304}; await load(false);
            assert(!("If-None-Match" in headers)); assert.equal(shown(F.nodes[0]), "unk");
            data.nodes[0][3] = "d"; data.nodes[0][18] = "down";
            response = {status:200, ok:true, json:async()=>data}; await load(false);
            assert.equal(shown(F.nodes[0]), "down");
          })();
        """)

    def test_gate3_5_vm_evidence_dated_in_share(self):
        self.rows[0]["Status"] = 2
        fm = self.model(view([vm("app.corp.example.mil")], self.now, age=181))
        self.js(fm, """
          openCard([F.by["1"]]); assert.equal(shown(F.by["1"]), "down");
          assert(card().includes("(LATE)")); assert(panel.text.includes("as of " + hm((data.now-181)*1000) + " (LATE)"));
          assert(window.SAWallShare.nodes.html(panel.rows, panel.ctx).includes("(LATE)"));
          const painted = [];
          document.createElement = () => ({getContext: () => ({
            scale(){}, fillRect(){}, fillText(t){painted.push(t);}, measureText(t){return {width:t.length*7};}
          })});
          window.SAWallShare.nodes.picture(panel.rows, panel.ctx);
          assert(painted.some(t => t.includes("heartbeat OK") && t.includes("(LATE)")));
          advance(600); refresh();
          assert(panel.rows[0].vc.includes("(NOT CURRENT)"));
        """)

    def test_gate3_6_slow_second_node_page(self):
        clock = [self.now]
        class Slow(FakeClient):
            def query(client, swql, params=None):
                if (params or {}).get("last") == 999:
                    clock[0] += 240
                return super().query(swql, params)
        rows = [sw_row(i, "N%d" % i) for i in range(1001)]
        client = Slow(rows, {"list": [], "members": []})
        sn = wf.Reader(lambda: client, OPTS, clock=lambda: clock[0]).read_once()
        fm = wf.model(sn, None, OPTS, clock[0])
        self.assertEqual(fm["counts"][wf.UP], 1)
        self.assertEqual([sn["nodes"][i]["_read_at"] for i in (0, 999, 1000)], [self.now, self.now, self.now + 240])
        self.assertEqual((fm["complete"], fm["verdict"]["up"], fm["verdict"]["state"]), (False, 1, "stale"))

    def test_gate3_7_full_dns_join(self):
        name = "x" * 40 + "." + "y" * 22 + ".corp.example.mil"
        self.assertEqual(len(name), 80)
        self.rows[0].update(DNS=name + ".dr.corp.example.mil", Status=2)
        fm = self.model(view([vm(name, power="poweredOff")], self.now))
        self.assertEqual(by_name(fm)["APP"][9], "")
        self.assertEqual(fm["verdict"]["state"], "crit")

    def test_gate3_8_integer_status_only(self):
        for status in (True, 1.5, "1", None):
            with self.subTest(status=status):
                self.rows[0]["Status"] = status
                fm = self.model()
                self.assertEqual(by_name(fm)["APP"][18:], ["unk", None])
                self.assertEqual(fm["verdict"]["state"], "stale")

    def test_gate3_9_service_tooltip_uses_members(self):
        self.rows[0]["Status"] = 2
        self.js(self.model(), """
          services(); assert.equal(gState(F.groups[0]).st, "crit");
          assert(!P.svc.children[0].attrs.title.includes("SolarWinds says"));
          assert(JSON.stringify(P.svc).includes("1 down"));
        """)

    def test_gate3_10_monitor_only_ad_reader_off(self):
        from test_netapp_live import cfg_for
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfg_for(tmp, PORTAL_NETMAP="0", PORTAL_AD="1", PORTAL_WALL_MONITOR="1",
                          PORTAL_WALL_MONITOR_SWIS_URL="https://swis.corp.example.mil",
                          PORTAL_SWIS_LOGIN=json.dumps({"user": "viewer", "password": "example"}))
            self.assertTrue(cfg.ad_swis_why)
            self.assertEqual(cfg.wall_fleet_why, "")
            self.assertEqual(cfg.swis_url, "")
        tasks = Path(ROOT, "roles/site_portal_stack/tasks/main.yml").read_text()
        for var, gate in (("PORTAL_SWIS_URL", "_portal_netmap"), ("PORTAL_WALL_MONITOR_SWIS_URL", "_portal_wall_monitor")):
            self.assertIn(var + ': "{{ portal_solarwinds_url | trim if ' + gate + " | bool else '' }}", tasks)

    def test_gate3_p2_2_last_known_poll_in_share(self):
        for status, word, poll in ((2, "DOWN", self.now - 86400), (3, "WARN", None)):
            self.rows[0].update(Status=status, LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(poll)) if poll else None)
            self.js(self.model(), """
              const n = F.by["1"]; openCard([n]);
              assert.equal(panel.rows[0].sev, "stale");
              const expected = n.polled ? "SolarWinds last polled " + hm(n.polled*1000) : "SolarWinds last poll unknown";
              assert(panel.text.startsWith("[" + ST_WORD[n.c] + " - last known, " + expected + "]"));
              assert(phrase(n).includes(expected));
            """)

    def test_gate3_p2_7_timeout_releases_busy(self):
        self.js(self.model(), """
          (async () => {
            let timeout, calls=0, ms;
            globalThis.setTimeout = (fn, delay) => { timeout=fn; ms=delay; };
            globalThis.fetch = (_, opts) => new Promise((_, reject) => {
              calls++; opts.signal.addEventListener("abort", () => reject(Error("timed out")));
            });
            const first = load(false); assert(BUSY); assert.equal(ms, 60000);
            timeout(); await first; assert(!BUSY); assert(FAIL); assert.equal(shown(F.nodes[0]), "unk");
            const second = load(false); assert.equal(calls, 2); timeout(); await second;
          })();
        """)


class Gate4(unittest.TestCase):
    setUp = Gate.setUp
    js = Gate.js
    model = Gate3.model

    def delivered(self):
        fm = self.model()
        fm["actions"], fm["wall"] = StaleNeverGreen().area(fm, self.now)
        return fm

    def test_gate4_1_board_obeys_wall_feed_failure(self):
        self.js(self.model(), """
          api.feed = () => ({state:"auth"}); api.feedFresh = () => "stale";
          assert.equal(shown(F.nodes[0]), "unk");
          assert.equal(gState(F.groups[0]).st, "stale");
          openCard([F.nodes[0]]); assert(panel.text.startsWith("[UNKNOWN]"));
        """)

    def test_gate4_2_failed_wall_body_invalidates_etag(self):
        self.js(self.delivered(), """
          (async () => {
            const requests = [];
            let response;
            globalThis.fetch = async (_, o) => { requests.push(o.headers); return response; };
            response = {ok:true, headers:{get:()=>"v1"}, json:async()=>data.wall};
            await window.loadWall(false);
            response = {ok:true, headers:{get:()=>"v2"}, json:async()=>{throw Error("truncated");}};
            await window.loadWall(false);
            assert.equal(requests[1]["If-None-Match"], "v1");
            response = {status:304}; await window.loadWall(false);
            assert.equal(requests[2]["If-None-Match"], undefined);
            assert.equal(api.areaState("monitor"), "stale");
          })();
        """)

    def test_gate4_3_older_wall_answer_ignored(self):
        self.js(self.delivered(), """
          (async () => {
            const replies = [];
            globalThis.fetch = () => new Promise(resolve => replies.push(resolve));
            const early = window.loadWall(false), later = window.loadWall(false);
            const outage = JSON.parse(JSON.stringify(data.wall)); outage.now++; outage.fleet.state = "crit";
            replies[1]({ok:true, json:async()=>outage}); await later;
            assert.equal(api.areaState("monitor"), "crit");
            replies[0]({ok:true, json:async()=>data.wall}); await early;
            assert.equal(api.areaState("monitor"), "crit");
          })();
        """)

    def test_gate4_4_all_three_deadlines_start_at_receipt(self):
        Gate3.expiring(self)
        self.js(self.delivered(), """
          (async () => {
            const bodies = [data, data.wall, {now:data.now, areas:[data.actions]}];
            globalThis.fetch = async () => ({status:200, ok:true, json:async()=>bodies.shift()});
            const pending = [load(false), window.loadWall(false), window.loadActions()];
            advance(12); await Promise.all(pending);
            assert.equal(shown(F.nodes[0]), "up"); advance(6);
            assert.equal(shown(F.nodes[0]), "unk");
            assert.equal(api.areaState("monitor"), "stale");
            assert.equal(window.actions().state, "stale");
          })();
        """)

    def test_gate4_5_copy_and_save_recompute_at_click(self):
        Gate3.expiring(self)
        self.js(self.model(), """
          (async () => {
            window.SAWallShare.nodes.panel = realPanel;
            const painted = [], saved = [], copies = [];
            document.createTextNode = t => t; document.body = el();
            document.createElement = tag => Object.assign(el(tag), {
              setAttribute(){}, getContext:()=>({scale(){}, fillRect(){}, fillText(t){painted.push(t);},
                measureText(t){return {width:t.length*6};}}),
              toBlob: cb => cb(new Blob([painted.join(" ")])), toDataURL:()=>painted.join(" "),
              click(){saved.push(this.href);}
            });
            window.ClipboardItem = globalThis.ClipboardItem = class { constructor(parts){this.parts=parts;} };
            window.isSecureContext = true;
            globalThis.navigator = {clipboard:{write:async items => copies.push(items[0].parts)}};
            openCard([F.nodes[0]]);
            const buttons = P.ov.querySelectorAll("button");
            const click = t => buttons.find(b => b.children[0] === t).listeners.click();
            assert(painted.includes("UP")); painted.length = 0; advance(6);
            click("Copy both");
            assert((await copies[0]["text/plain"].text()).startsWith("[UNKNOWN]"));
            assert((await copies[0]["text/html"].text()).includes("[UNKNOWN]"));
            assert((await (await copies[0]["image/png"]).text()).includes("UNKNOWN"));
            painted.length = 0; click("Save picture"); assert(saved[0].includes("UNKNOWN"));
          })();
        """)

    def test_gate4_6_down_and_conditions_ignore_vcenter(self):
        self.rows[0]["Status"] = 2
        base = self.model()
        for power in ("poweredOn", "poweredOff", "suspended"):
            fm = self.model(view([vm("app.corp.example.mil", power=power)], self.now, age=179))
            self.assertEqual(by_name(fm)["APP"][18:], by_name(base)["APP"][18:])
            self.assertEqual(fm["verdict"], base["verdict"])
            self.assertEqual(fm["next_expiry"], base["next_expiry"])
            self.assertEqual(wf.conds(fm, self.now), wf.conds(base, self.now))
            self.assertNotIn("vCenter", str(wf.conds(fm, self.now)))

    def test_gate4_7_unknown_status_suppresses_up_text(self):
        self.rows[0].update(Status=True, StatusDescription="Node status is Up.")
        self.js(self.model(), """
          const fields = cardDetails(F.by["1"], shown(F.by["1"])).filter(Boolean);
          assert(fields.some(([k,v]) => k === "SolarWinds says" && !v.includes("Node status is Up.")));
          assert(!fields.find(([k]) => k === "Status")[1].includes("Up"));
        """)

    def test_gate4_8_future_poll_stays_unknown(self):
        row = dict(self.rows[0], LastSync=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now + 181)))
        sn = snap([row], now=self.now, age=0)
        for elapsed in (0, 61):
            self.assertEqual(wf.model(sn, None, OPTS, self.now + elapsed)["nodes"][0][18:], ["unk", None])

    def test_gate4_9_keyset_survives_deletion_and_probes_cap(self):
        class Changing(FakeClient):
            def query(client, swql, params=None):
                if client.q:
                    client.rows = client.rows[1:]
                return super().query(swql, params)
        rows = [sw_row(i, str(i), status=2 if i == 1001 else 1) for i in range(1, 1003)]
        client = Changing(rows)
        read, cut, _ = wf.read_nodes(client, OPTS)
        self.assertFalse(cut)
        self.assertEqual([r["NodeID"] for r in read if r["Status"] == 2], [1001])
        self.assertIn("WHERE N.NodeID > @last ORDER BY N.NodeID", client.q[-1][0])
        for length in (1000, 1001):
            client = FakeClient(rows[:length])
            read, cut, _ = wf.read_nodes(client, dict(OPTS, max_nodes=1000))
            self.assertEqual((len(read), cut), (1000, length > 1000))
            self.assertIn("SELECT TOP 1 ", client.q[-1][0])

    def test_gate4_10_unreadable_membership_blocks_recovery(self):
        for field in ("ContainerID", "MemberPrimaryID"):
            for bad in (None, True, 1.5, "?", []):
                members = [dict(ContainerID=1, MemberPrimaryID=1, MemberEntityType="Orion.Nodes")]
                members.append(dict(members[0], **{field: bad}))
                client = FakeClient(self.rows, {"list":[dict(ContainerID=1, Name="Service", Status=2)], "members":members})
                fm = wf.model(wf.Reader(lambda: client, OPTS).read_once(), None, OPTS, self.now)
                self.assertFalse(fm["complete"], (field, bad))
                self.assertFalse(wf.conds(fm, self.now)[1])

    def test_gate4_11_membership_deadline_blocks_recovery(self):
        sn = snap(self.rows, self.groups, self.now)
        sn["groups_at"] = self.now - wf.GROUPS_EVERY - 2 * OPTS["seconds"] + 1
        fm = wf.model(sn, None, OPTS, self.now)
        self.assertEqual(fm["verdict"]["valid_until"], self.now + 1)
        self.assertEqual(fm["groups"][0][3]["valid_until"], self.now + 1)
        self.assertFalse(wf.conds(fm, self.now + 1)[1])
        fm = wf.model(sn, None, OPTS, self.now + 2)
        self.assertTrue(all(g[3]["state"] == "stale" for g in fm["groups"]))
        self.assertEqual(by_name(fm)["APP"][18], "up")
        self.rows[1]["Status"] = 2
        fm = wf.model(sn, None, OPTS, self.now + 2)
        self.assertEqual(by_name(fm)["DB"][18], "down")
        self.assertEqual(wf.conds(fm, self.now + 2), ([], []))

    def test_gate4_12_slow_inventory_cannot_suppress_outage(self):
        self.rows[1]["Status"] = 2
        for age in (0, 179, 240):
            fm = self.model(view([vm("db.corp.example.mil", power="poweredOff")], self.now, age=age))
            area, _ = StaleNeverGreen().area(fm, self.now)
            self.assertEqual((area["state"], area["big"], area["valid_until"]), ("crit", "1/2", self.now + 150))

    def test_gate4_13_vcenter_inventory_only(self):
        fm = self.model(view([vm("extra", hb="green", tools="guestToolsNotRunning")], self.now))
        self.assertEqual(fm["verdict"]["total"], 2)
        self.js(fm, """
          const n = F.nodes.find(n => n.vconly);
          assert.equal(shown(n), "info"); assert.equal(share(n).st, "INFO");
          assert.equal(gState(F.groups[n.g]).st, "info"); assert.equal(counts().up, 2);
          assert(coverageCurrent()); advance(180); assert(!coverageCurrent());
        """)

    def test_gate4_14_unique_exact_match_only(self):
        for guests, note in (([vm("APP")], ""), ([vm("other", guest="APP.CORP.EXAMPLE.MIL.")], "1"),
                             ([vm("other", ip="192.0.2.10")], "1"),
                             ([vm("app.corp.example.mil"), vm("other", ip="192.0.2.10")], "")):
            self.rows[1]["IPAddress"] = "192.0.2.11"
            fm = self.model(view(guests, self.now))
            self.assertEqual(by_name(fm)["APP"][9], note)
        self.js(self.model(view([vm("app.corp.example.mil")], self.now)), """
          openCard([F.by["1"]]);
          const note = "vCenter says: powered on · heartbeat OK, as of " + hm((data.now-20)*1000);
          assert(panel.text.includes(note)); assert(window.SAWallShare.nodes.html(panel.rows,panel.ctx).includes(note));
          openCard([F.by["2"]]); assert(!panel.text.includes("vCenter says:")); assert(!card().includes("vCenter says:"));
        """)
        vc = view([vm("app.corp.example.mil")], self.now)
        vc["sources"]["vcenter:b"] = dict(kind="vcenter", state="never", at=None)
        self.assertEqual(by_name(self.model(vc))["APP"][9], "")

    def test_gate4_15_non_unicast_ips_never_match(self):
        for addr in ("::", "0.0.0.0", "fe80::1", "127.0.0.1", str(__import__("ipaddress").IPv4Address(0xE0000001)), "::1", "169.254.1.2"):
            self.rows[0]["IPAddress"] = addr
            fm = self.model(view([vm("other", ip=addr)], self.now))
            self.assertEqual(by_name(fm)["APP"][9], "", addr)

    def test_gate4_16_storage_keeps_capacity_after_late(self):
        fm = self.model()
        fm["actions"] = dict(id="storage", state="ok", big="70%", unit="used", note="aggr2",
                             ok_until=self.now + 1, metric=dict(asof=self.now - 5, state="ok", value=70))
        self.js(fm, """
          advance(2); const a = window.actions();
          assert.deepEqual([a.state,a.big,a.unit,a.note,a.metric.state], ["stale","70%","used","aggr2","ok"]);
        """)

    def test_gate4_17_settings_only_when_enabled(self):
        from unittest.mock import patch
        from test_netapp_live import cfg_for
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(wf, "opts_of", side_effect=AssertionError("monitor settings read while disabled")):
                for wall, monitor in (("0","1"), ("1","0")):
                    cfg_for(tmp, PORTAL_WALL=wall, PORTAL_WALL_MONITOR=monitor, PORTAL_WALL_OPTS='{"monitor_seconds":1e309}')
        for key, low, high in (("seconds",60,3600), ("max_nodes",100,50000), ("poll_stale_minutes",5,1440)):
            for value in (float("inf"), None, "invalid"):
                opts, notes = wf.opts_of({"monitor_" + key: value})
                self.assertEqual(opts[key], wf.DEFAULTS[key])
                self.assertTrue(notes)
            for value, expected in ((-100, low), (10**20, high)):
                self.assertEqual(wf.opts_of({"monitor_" + key: value})[0][key], expected)

    def test_follower_does_not_register_as_monitor_viewer(self):
        from unittest.mock import Mock, patch
        from site_portal import WallLive
        live = Mock()
        live.cfg.wall_opts = {"seconds":60}
        live.viewed.side_effect = live.fleet.viewed
        with patch("site_portal.time.sleep", side_effect=[None, KeyboardInterrupt]), self.assertRaises(KeyboardInterrupt):
            WallLive.follow(live)
        live.nal.viewed.assert_called_once()
        live.fleet.viewed.assert_not_called()


class Gate5(unittest.TestCase):
    setUp = Gate.setUp
    js = Gate.js
    model = Gate3.model
    delivered = Gate4.delivered
    role = Path(FILES).parent

    def test_gate5_1_cross_endpoint_verdict(self):
        self.js(self.delivered(), """
          (async () => {
            data.seq = 1; data.wall.seq = 2; data.wall.fleet.state = "crit";
            F = adapt(data); api.W = window.wallModel; api.feed = ()=>({state:"ok"}); api.feedFresh = ()=> "ok";
            assert.equal(shown(F.nodes[0]), "up");
            await wallReply(data.wall);
            assert.equal(shown(F.nodes[0]), "unk");
            data.seq = 3; data.verdict.state = "crit"; F = adapt(data);
            data.wall.seq = 2; data.wall.fleet.state = "ok"; await wallReply(data.wall);
            assert.notEqual(api.areaState("monitor"), "ok");
          })();
        """)

    def test_gate5_2_delayed_wall_replies(self):
        self.js(self.delivered(), """
          (async () => {
            let resolve;
            const held = () => {globalThis.fetch = () => new Promise(r => resolve = r); return window.loadWall(false);};
            const old = held();
            globalThis.fetch = async () => ({status:503}); await window.loadWall(false);
            resolve({ok:true,json:async()=>data.wall}); await old;
            assert.equal(api.areaState("monitor"), "stale");
            await wallReply({...data.wall, seq:2});
            const cached = held(); await wallReply({...data.wall, seq:3});
            resolve({status:304}); await cached;
            assert.equal(api.areaState("monitor"), "ok");
            globalThis.fetch = async () => ({status:503, json:async()=>({seq:5})}); await window.loadWall(false);
            await wallReply({...data.wall, seq:4}); assert.equal(api.areaState("monitor"), "stale");
            await wallReply({...data.wall, seq:6}); assert.equal(api.areaState("monitor"), "ok");
          })();
        """)

    def test_gate5_3_monotonic_receipt(self):
        Gate3.expiring(self)
        self.js(self.delivered(), """
          (async () => {
            await deliverAll(); advance(6); shiftDate(-60);
            assert.deepEqual([shown(F.nodes[0]),api.areaState("monitor"),window.actions().state],
                             ["unk","stale","stale"]);
          })();
        """)

    def answers(self):
        from threading import Lock
        from test_netapp_live import cfg_for
        from site_portal import WallLive
        live = Gate.live(self, snap(self.rows, self.groups, self.now))
        with tempfile.TemporaryDirectory() as tmp:
            live.cfg = cfg_for(tmp, PORTAL_WALL_MONITOR="1")
            live.collector, live.lock, live.nal, live.tracker = None, Lock(), None, wm.Tracker()
            return live.fleet_answer()[1], WallLive.answer(live), live.fleet_answer()[1]

    def test_gate5_4_shared_sequence_and_clock_reset(self):
        a, (_, b, _), c = self.answers()
        seq = [json.loads(x)["seq"] for x in (a,b,c)]
        self.assertTrue(all(type(n) is int for n in seq) and seq[0] < seq[1] < seq[2])
        self.js(self.delivered(), """
          (async () => {
            await wallReply({...data.wall, seq:10});
            await wallReply({...data.wall, seq:12, now:data.now-60, title:"newer"});
            await wallReply({...data.wall, seq:11, title:"obsolete"});
            assert.equal(window.wallModel().title,"newer");
            for (const seq of [12,11]) {
              globalThis.fetch = async()=>({status:200,json:async()=>({...data,seq,title:String(seq)})});
              await load(false);
            }
            assert.equal(F.title,"12");
          })();
        """)

    def test_gate5_5_hit_current_row(self):
        self.js(self.model(), """
          const old = F.groups[0];
          LAY.blocks = [{g:old,name:old.name,kind:old.kind,keys:old.nodes.map(n=>n.key),x:0,y:0,top:10,w:100,rows:1,per:10}];
          P.cv = {clientWidth:100,getBoundingClientRect:()=>({left:0,top:0,width:100})};
          data.nodes[0][18] = "down"; F = adapt(data);
          const latest = hit({clientX:1,clientY:12}).n;
          assert.strictEqual(latest,F.by[old.nodes[0].key]); assert.equal(shown(latest),"down");
        """)

    def test_gate5_6_refresh_condition_share(self):
        self.js(self.delivered(), """
          window.refreshShare(); assert.equal(window.sharedCondition.sev,"crit");
          assert.equal(window.sharedCondition.until,"");
        """)

    def test_gate5_7_membership_cursor(self):
        members = [dict(ContainerID=1, MemberPrimaryID=i, MemberEntityType="Orion.Nodes") for i in range(1,1003)]
        class Changing(FakeClient):
            def query(client, query, params=None):
                page = super().query(query, params)
                if "FROM Orion.ContainerMembers" in query and len(members) == 1002:
                    members.pop(0)
                return page
        client = Changing([], {"list": [dict(ContainerID=1, Name="DB", Status=2)], "members": members})
        groups = wf.read_groups(client, OPTS)
        self.assertIn("1001", groups["members"])
        self.assertFalse(groups["cut"])
        self.assertIn("ContainerID > @container OR (ContainerID = @container AND MemberPrimaryID > @member)", client.q[-1][0])
        self.assertTrue(client.q[-1][0].endswith("ORDER BY ContainerID, MemberPrimaryID"))

    def test_gate5_11_contradictory_status(self):
        self.rows[0].update(Status=2, PercentLoss=0, StatusDescription="Node status is Up.")
        self.js(self.model(), """
          const n = F.by["1"], fields = cardDetails(n, shown(n));
          assert(!JSON.stringify(fields).includes("Node status is Up."));
          assert(fields.some(r=>r && r[0]==="SolarWinds says"));
          openCard([n]); assert(!panel.text.includes("Node status is Up."));
        """)

    def test_gate5_12_conflicting_node_id(self):
        self.rows = [sw_row(1,"A-down",2),sw_row(1,"Z-up")]
        fm = self.model()
        self.assertEqual([r[18] for r in fm["nodes"] if r[13]=="1"], ["unk"])
        self.assertFalse(fm["complete"])

    def test_gate5_13_invalid_deadlines(self):
        for value in ("invalid", float("inf"), float("nan")):
            wall = self.delivered()["wall"]
            wall["fleet"]["valid_until"] = value
            summary = wm.summary(wall, self.now)
            area = next(a for a in summary["areas"] if a["id"] == "monitor")
            self.assertEqual((area["state"], area["valid_until"]), ("stale", None))
        self.js(self.delivered(), """
          (async () => {
            for (const bad of ["invalid","Infinity",null]) {
              data.actions.valid_until = data.wall.fleet.valid_until = bad;
              data.nodes[0][19] = data.groups[0][3].valid_until = bad;
              await deliverAll();
              assert.deepEqual([window.actions().state,api.areaState("monitor"),shown(F.nodes[0]),gState(F.groups[0]).st],
                               ["stale","stale","unk","stale"]);
            }
          })();
        """)

    def test_gate5_14_setting_types(self):
        from test_netapp_live import cfg_for
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp)/"settings.json"
            for url in ([],{},123):
                settings.write_text(json.dumps({"PORTAL_WALL_MONITOR":"1","PORTAL_WALL_MONITOR_SWIS_URL":url}))
                cfg = cfg_for(tmp, PORTAL_SETTINGS=str(settings))
                self.assertTrue(cfg.wall_fleet_why)
                self.assertTrue(cfg.wall_fleet_notes)
            for switch in (1, [], {}, "invalid"):
                settings.write_text(json.dumps({"PORTAL_WALL_MONITOR": switch}))
                cfg = cfg_for(tmp, PORTAL_SETTINGS=str(settings))
                self.assertFalse(cfg.wall_fleet)
                self.assertTrue(cfg.wall_fleet_notes)
            for key, value in (("seconds",True),("max_nodes",200.5),("groups",[2]),("vm_match",None),("group_by",[])):
                opts, notes = wf.opts_of({"monitor_"+key:value})
                self.assertEqual(opts[key],wf.DEFAULTS[key])
                self.assertTrue(notes,key)

    def test_gate5_15_history_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"since.json"
            for value in (float("inf"),float("nan"),10**400,-1):
                path.write_text(json.dumps({"d":{"sw:1":["d",value,True]},"at":{"sw:":value}}))
                history = wf.Since(path)
                self.assertEqual((history.d,history.last),({},{}))

    def test_gate5_16_surrogate_response_bytes(self):
        import hashlib
        self.rows[0].update(Caption="WEB\ud800", Status=2)
        a, (_, b, tag), _ = self.answers()
        for body in (a,b):
            self.assertIn(r"WEB\ud800", json.dumps(json.loads(body)))
            body.encode("utf-8")
        self.assertEqual(tag, '"%s"' % hashlib.sha256(b.encode()).hexdigest()[:20])

    def test_gate5_17_islands_disjoint(self):
        self.js(self.delivered(), """
          const old = [[150,125],[400,100],[640,140],[860,100],[1070,135]];
          const bounds = window.mapBounds();
          assert.deepEqual(bounds.slice(0,5).map(b=>b.center),old);
          bounds.forEach((a,i)=>bounds.slice(i+1).forEach(b=>assert(
            a.right < b.left || b.right < a.left || a.bottom < b.top || b.bottom < a.top)));
        """)

    def test_gate5_18_solarwinds_ca_is_private(self):
        import yaml
        tasks = yaml.safe_load((self.role/"tasks/main.yml").read_text())
        copy = next(t["ansible.builtin.copy"] for t in tasks if t.get("ansible.builtin.copy",{}).get("src")=="{{ portal_solarwinds_ca_file }}")
        self.assertNotIn("/trust.d/",copy["dest"])
        import site_portal as sp
        from unittest.mock import patch
        from test_netapp_live import cfg_for
        with tempfile.TemporaryDirectory() as tmp:
            cfg = cfg_for(tmp, PORTAL_NETMAP="0", PORTAL_WALL_MONITOR="1", PORTAL_SWIS_CA="/app/solarwinds.pem",
                          PORTAL_WALL_MONITOR_SWIS_URL="https://swis.corp.example.mil",
                          PORTAL_SWIS_LOGIN=json.dumps({"user": "reader", "password": "example"}))
            with patch.object(sp.site_aap, "Api") as aap, patch.object(site_swis, "Client") as swis:
                sp.Aap(cfg)
                sp.WallLive(cfg, None).fleet.client_fn()
                self.assertTrue(aap.call_args.args[0]["verify"])
                self.assertEqual(aap.call_args.kwargs["ca_path"], "")
                self.assertEqual(swis.call_args.kwargs["ca_path"], "/app/solarwinds.pem")

    def test_gate5_19_deploy_assert_only_when_enabled(self):
        import yaml
        tasks = yaml.safe_load((self.role/"tasks/main.yml").read_text())
        assertion = next(t for t in tasks if t["name"].endswith("the wall's Ops monitor settings"))
        self.assertEqual(assertion["when"], "_portal_wall_monitor | bool")

    def test_gate5_20_preview_opt_in(self):
        import yaml
        self.assertIs(yaml.safe_load((self.role/"defaults/main.yml").read_text())["portal_wall_monitor"],False)


NODE_JS = r"""
const fs = require("fs"), vm = require("vm");
const doc = {createElement: () => ({getContext: () => null, style: {}}), fonts: null};
const ctx = {window: {}, document: doc, navigator: {}, console: console};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const N = ctx.window.SAWallShare.nodes;
const L = JSON.parse(process.argv[3]), c = JSON.parse(process.argv[4]);
process.stdout.write(JSON.stringify({text: N.text(L, c), html: N.html(L, c), line: L.map(N.line)}));
"""


@unittest.skipUnless(shutil.which("node"), "no node")
class ShareText(unittest.TestCase):
    def run_js(self, L, c):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        js = os.path.join(d, "t.js")
        with open(js, "w") as fh:
            fh.write(NODE_JS)
        out = subprocess.run(["node", js, os.path.join(FILES, "static", "wall-share.js"), json.dumps(L), json.dumps(c)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_the_lines_the_user_asked_for(self):
        L = [{"st": "DOWN", "sev": "crit", "name": "SITEADIRDCV01", "svc": "Active Directory", "ip": "192.0.2.12", "phrase": "down 21 min (since 05:21)"},
             {"st": "WARN", "sev": "warn", "name": "SITEACERTV02", "svc": "Certificate Services", "ip": "", "phrase": "packet loss 8% (12 min, since 05:30)"}]
        c = {"site": "SITEA", "stamp": "05:42", "day": "26 Nov", "zone": "Berlin", "src": "SolarWinds every 60 s", "link": "https://portal.example.mil/portal/wall?area=monitor", "stale": ""}
        o = self.run_js(L, c)
        self.assertEqual(o["line"][0], "DOWN  SITEADIRDCV01 - Active Directory - 192.0.2.12 - down 21 min (since 05:21)")
        self.assertEqual(o["line"][1], "WARN  SITEACERTV02 - Certificate Services - no IP - packet loss 8% (12 min, since 05:30)")
        self.assertTrue(o["text"].startswith("2 nodes not up (1 down) - SITEA - 05:42\nDOWN  SITEADIRDCV01"))
        self.assertTrue(o["text"].endswith("\nWall: https://portal.example.mil/portal/wall?area=monitor"))

    def test_everything_escaped(self):
        bad = '<img src=x onerror="alert(1)">&\'"'
        L = [{"st": "DOWN", "sev": "crit", "name": bad, "svc": bad, "ip": bad, "phrase": bad}]
        c = {"site": bad, "stamp": "05:42", "day": "", "zone": "", "src": bad, "link": 'javascript:"><script>x()</script>', "stale": bad}
        o = self.run_js(L, c)
        h = o["html"]
        self.assertNotIn("<img", h)
        self.assertNotIn("<script", h)
        self.assertNotIn('onerror="', h)
        self.assertIn("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&amp;&#39;&quot;", h)
        self.assertIn('<a href="#">', h)                                     # http(s) links only (the page makes it from location.origin)
        many = [dict(L[0], name="N%03d" % i) for i in range(250)]
        o = self.run_js(many, dict(c, site="S"))
        self.assertEqual(o["text"].count("\nDOWN  "), 200)
        self.assertIn("+50 more on the wall", o["text"])


class Deploy(unittest.TestCase):
    def test_settings_reach_the_service(self):
        import yaml
        d = yaml.safe_load(open(os.path.join(ROOT, "roles", "site_portal_stack", "defaults", "main.yml")))
        for k in ("portal_wall_monitor", "portal_wall_monitor_seconds", "portal_wall_monitor_group_by", "portal_wall_monitor_groups", "portal_wall_monitor_vm_match",
                  "portal_wall_monitor_max_nodes", "portal_wall_monitor_poll_stale_minutes"):
            self.assertIn(k, d)
        t = open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")).read()
        for k in ("PORTAL_WALL_MONITOR:", "monitor_seconds:", "monitor_group_by:", "monitor_groups:", "monitor_vm_match:", "monitor_max_nodes:", "monitor_poll_stale_minutes:"):
            self.assertIn(k, t)
        self.assertIn('_portal_has_swis: "{{ _portal_swis | bool }}"', t)                       # the login deployed for the Ops monitor area too
        self.assertIn("PORTAL_SWIS_URL: \"{{ portal_solarwinds_url | trim if _portal_netmap | bool else '' }}\"", t)
        import site_portal_filters as F
        base = {"wall": True, "vc_on": True, "ontap_on": True, "netmap_sites": {}}
        self.assertEqual(F.site_portal_wall_summary(base), "Wall: VMware on, Storage on, Map places from netmap_sites: 0")
        self.assertEqual(F.site_portal_wall_summary(dict(base, monitor_on=True, monitor_group_by="custom:ServiceGroup")),
                         "Wall: VMware on, Storage on, Map places from netmap_sites: 0, Ops monitor on (by custom:ServiceGroup)")
        self.assertTrue(F.site_portal_wall_summary(dict(base, monitor_on=False, sw_url=False)).endswith("Ops monitor off (no SolarWinds address (SOLARWINDS_URL / netmap_solarwinds_url empty))"))

    def test_the_service_settings(self):
        import site_portal as sp
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        T = __import__("test_netapp_live")
        cfg = T.cfg_for(d, PORTAL_WALL_MONITOR="1")
        self.assertTrue(cfg.wall_fleet)
        self.assertIn("no SolarWinds address", cfg.wall_fleet_why)
        cfg = T.cfg_for(d, PORTAL_WALL_MONITOR="1", PORTAL_WALL_MONITOR_SWIS_URL="https://swis.example.invalid", PORTAL_SWIS_LOGIN=json.dumps({"user": "u", "password": "p"}),
                        PORTAL_WALL_OPTS=json.dumps({"monitor_group_by": "custom:Bad Name", "monitor_seconds": 120}))
        self.assertEqual((cfg.wall_fleet_why, cfg.wall_fleet_opts["seconds"], cfg.wall_fleet_opts["group_by"]), ("", 120, "groups"))
        self.assertTrue(cfg.wall_fleet_notes)
        cfg = T.cfg_for(d, PORTAL_WALL_MONITOR="1", PORTAL_WALL="0")
        self.assertFalse(cfg.wall_fleet)


def browser():
    for b in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(b):
            return shutil.which(b)
    return None


@unittest.skipUnless(browser(), "no google-chrome or chromium")
class Page(unittest.TestCase):
    """the real page from a local portal with the fleet demo (tests/wall/demo.py + fleet_demo.py)"""

    @classmethod
    def setUpClass(cls):
        import demo
        cls.fx = fd.fixture()
        cls.url, cls.stop = demo.portal(vc2_state="ok", fleet=fd.FakeReader(cls.fx), collector=demo.FakeCollector(lambda: fd.view(fx=cls.fx)))

    @classmethod
    def tearDownClass(cls):
        cls.stop()

    def dom(self, query, width=1920, height=1080, url=None):
        args = [browser(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--window-size=%d,%d" % (width, height),
                "--virtual-time-budget=8000", "--dump-dom", (url or self.url) + "wall?" + query]
        return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=180).stdout

    def test_every_style_draws_the_area(self):
        for st in ("strata", "relay", "metro", "cockpit", "lights", "map"):
            out = self.dom("area=monitor&style=%s&freeze=1" % st)
            self.assertIn('data-ready="1"', out, st)
            self.assertIn('class="wx wx-fleet"', out, st)
            self.assertIn("13 down", out, st)
            self.assertIn("SITEADIRDCV01", out, st)                     # the Not up list
            self.assertIn("VMs in vCenter, not monitored in SolarWinds", out, st)
            self.assertRegex(out, r"group \d+ of \d+ · problems first", st)    # the Spotlight's cue
            self.assertNotIn("<img src=\"x\"", out, st)                  # the markup in a caption stays text
        out = self.dom("style=strata&freeze=1")
        self.assertIn("Ops monitor", out)                      # its tile in the room's overview
        self.assertIn("Network", out)

    def test_legacy_area_url_opens_monitor(self):
        out = self.dom("area=fleet&style=strata&freeze=1")
        self.assertIn('aria-label="Ops monitor"', out)

    def test_card_and_bulk(self):
        out = self.dom("area=monitor&style=strata&monitor=one&freeze=1")
        self.assertIn('class="fl-ov on"', out)
        self.assertIn("Copy picture", out)
        self.assertIn("Copy both", out)
        self.assertIn("Save picture", out)
        self.assertIn("Add to selection", out)
        out = self.dom("area=monitor&style=metro&monitor=bulk&freeze=1")
        self.assertIn("Share 13 nodes to Teams", out)
        self.assertIn("13 selected", out)
        self.assertIn("Wall: " + self.url + "wall?area=monitor", out)

    def test_stale_solarwinds_is_never_green(self):
        import demo
        url, stop = demo.portal(vc2_state="ok", fleet=fd.FakeReader(self.fx, age_s=900), collector=demo.FakeCollector(lambda: fd.view(fx=self.fx, vc_age=900)))
        self.addCleanup(stop)
        for st in ("strata", "metro", "cockpit"):
            out = self.dom("area=monitor&style=%s&freeze=1" % st, url=url)
            self.assertIn('data-ready="1"', out, st)
            board = out[out.index('class="wx wx-fleet"'):]
            self.assertNotIn('class="word ok"', board, st)               # no green word on the board: SolarWinds 15 min old
            self.assertIn("NOT CURRENT", board, st)
            self.assertIn("0 up", board, st)
            self.assertIn("no current data", board, st)


if __name__ == "__main__":
    unittest.main(verbosity=1)
