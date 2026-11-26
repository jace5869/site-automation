#!/usr/bin/env python3
"""1.138.0: the wall's Fleet & services area (roles/site_portal_stack/files/wall_fleet.py, static/wall-fleet.js, the node share in
static/wall-share.js): the grouping (SolarWinds groups, a custom property, the configured list), the vCenter compare (powered off is
never down, Disagree, vCenter only, not found, two VMs of a name), stale never green, the since-tracker, 3,000 nodes, the payload,
the reader (SWQL SELECTs only, a refused login, a column Orion has not) against the fake SWIS, the share text and HTML (escaped),
and the page in each style (headless Chrome). Test data only (tests/wall/fleet_demo.py)."""
import json
import os
import re
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
        o, bad = wf.opts_of({"fleet_seconds": 5, "fleet_group_by": "custom:ServiceGroup", "fleet_groups": ["AD", 3, ""]})
        self.assertEqual((o["seconds"], o["group_by"], o["groups"]), (60, "custom:ServiceGroup", ["AD"]))
        for gb in ("custom:Service Group", "custom:x;DROP", "custom:", "custom:1abc", "Location"):   # SWQL text: letters, digits, _ only
            o, bad = wf.opts_of({"fleet_group_by": gb})
            self.assertEqual(o["group_by"], "groups", gb)
            self.assertTrue(bad, gb)
        self.assertEqual(wf.opts_of({"fleet_seconds": 99999})[0]["seconds"], 3600)

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
        o = wf.opts_of({"fleet_groups": ["gamma", "ALPHA", "Nope"]})[0]
        fm = wf.model(snap(rows, groups, self.now), None, o, self.now)
        self.assertEqual([g[0] for g in fm["groups"]], ["Gamma", "Alpha", wf.NO_GROUP])
        self.assertEqual(by_name(fm)["B1"][0], 2)                       # Beta not listed: under Workloads (no group)

    def test_a_custom_property(self):
        rows = [sw_row(1, "A1", cp="Active Directory"), sw_row(2, "A2", cp="active directory"), sw_row(3, "B1", cp=""), sw_row(4, "C1", cp=None)]
        o = wf.opts_of({"fleet_group_by": "custom:ServiceGroup"})[0]
        fm = wf.model(snap(rows, now=self.now), None, o, self.now)
        self.assertEqual([g[0] for g in fm["groups"]], ["Active Directory", wf.NO_GROUP])     # any case: one group, its first spelling
        self.assertEqual(fm["groups"][0][1], "c")
        self.assertEqual([r[0] for r in fm["nodes"]], [0, 0, 1, 1])


class Compare(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def fm(self, rows, vms, age=20, state="ok", o=OPTS):
        return wf.model(snap(rows, now=self.now), view(vms, self.now, age, state), o, self.now)

    def test_powered_off_is_never_down(self):
        rows = [sw_row(1, "SITEAAPPV031", status=2), sw_row(2, "SITEAAPPV032", status=2)]
        fm = self.fm(rows, [vm("siteaappv031.corp.example.mil", power="poweredOff", hb=None), vm("SITEAAPPV032", power="suspended", hb=None)])
        n = by_name(fm)
        self.assertEqual((n["SITEAAPPV031"][3], n["SITEAAPPV032"][3]), ("p", "p"))
        self.assertIn("powered off in vCenter", n["SITEAAPPV031"][8])
        self.assertIn("suspended", n["SITEAAPPV032"][8])
        self.assertEqual(fm["cmp"]["off_in_vc"], 2)
        self.assertEqual(wf.conds(fm, self.now)[0], [])                 # nothing down: no condition

    def test_disagree_only_with_a_green_heartbeat(self):
        rows = [sw_row(1, "SITEASQLV05", status=2), sw_row(2, "SITEADIRDCV01", status=2), sw_row(3, "SITEAX03", status=2)]
        fm = self.fm(rows, [vm("SITEASQLV05"), vm("siteadirdcv01", hb="gray", tools="guestToolsNotRunning"), vm("SITEAX03", hb=None)])
        n = by_name(fm)
        self.assertEqual(n["SITEASQLV05"][3], "d")                      # still down - flagged, never hidden
        self.assertIn("g", n["SITEASQLV05"][12])
        self.assertIn("check the network path", n["SITEASQLV05"][8])
        for nm in ("SITEADIRDCV01", "SITEAX03"):                       # heartbeat grey or not read: unknown, no flag
            self.assertEqual(n[nm][3], "d")
            self.assertNotIn("g", n[nm][12])
        self.assertEqual((fm["cmp"]["disagree"], fm["cmp"]["disagree_names"]), (1, ["SITEASQLV05"]))

    def test_a_stale_vcenter_never_turns_red_grey(self):
        rows = [sw_row(1, "SITEAAPPV031", status=2), sw_row(2, "SITEASQLV05", status=2)]
        vms = [vm("SITEAAPPV031", power="poweredOff", hb=None), vm("SITEASQLV05")]
        for age, state in ((900, "ok"), (20, "error"), (None, "never")):
            fm = self.fm(rows, vms, age=age, state=state)
            n = by_name(fm)
            self.assertEqual(n["SITEAAPPV031"][3], "d", (age, state))
            self.assertNotIn("g", n["SITEASQLV05"][12])
            self.assertIn("s", n["SITEAAPPV031"][12])                    # shown: not current, not compared
            self.assertEqual(fm["cmp"]["off_in_vc"], 0)

    def test_vcenter_only_and_not_found(self):
        rows = [sw_row(1, "SITEAOLDV01"), sw_row(2, "SITEASWACC001", vendor="Cisco", model="Catalyst 9300"), sw_row(3, "SITEAAPPV001")]
        vms = [vm("SITEAAPPV001"), vm("SITEAVMX01"), vm("SITEAVMX02", hb="red"), vm("SITEAVMX03", power="poweredOff", hb=None),
               vm("SITEAVMX04", hb=None, tools="guestToolsNotRunning"), vm("TEMPLATE01", template=True), vm("vCLS-1", vcls=True)]
        fm = self.fm(rows, vms)
        n = by_name(fm)
        self.assertEqual(fm["cmp"]["not_in_vc"], 1)                    # a VM by Vendor / MachineType; the switch is no VM
        self.assertIn("n", n["SITEAOLDV01"][12])
        self.assertNotIn("n", n["SITEASWACC001"][12])
        self.assertEqual(fm["groups"][-1][0], wf.VC_ONLY)
        vco = dict((r[1], r[3]) for r in fm["nodes"] if "v" in r[12])
        self.assertEqual(vco, {"SITEAVMX01": "u", "SITEAVMX02": "d", "SITEAVMX03": "p", "SITEAVMX04": "d"})   # Tools ran before (tools_seen) and stopped
        self.assertNotIn("TEMPLATE01", vco)
        self.assertEqual(fm["cmp"]["vc_only"], 4)
        self.assertEqual([c for c in wf.conds(fm, self.now)[0] if "vCenter only" in c["id"]], [])   # the VMware area's, not here

    def test_not_found_is_not_counted_before_every_vcenter_was_read(self):
        rows = [sw_row(1, "SITEAOLDV01")]
        fm = wf.model(snap(rows, now=self.now), {"sources": {"vcenter:a": {"kind": "vcenter", "name": "a", "state": "ok", "at": self.now - 10, "snap": {"vms": []}},
                                                                "vcenter:b": {"kind": "vcenter", "name": "b", "state": "auth", "at": None, "snap": None}}}, OPTS, self.now)
        self.assertEqual(fm["cmp"]["not_in_vc"], 0)
        self.assertFalse(fm["cmp"]["vc_read"])

    def test_names_any_case_short_or_fqdn_and_two_of_a_name(self):
        rows = [sw_row(1, "SITEADB01", ip="192.0.2.21"), sw_row(2, "siteadb02.corp.example.mil", dns="", ip="192.0.2.22"),
                sw_row(3, "SITEADB03", dns="", ip="192.0.2.23"), sw_row(4, "192.0.2.40", dns="", ip="192.0.2.40")]
        vms = [vm("siteadb01.corp.example.mil", ip="192.0.2.21"), vm("SITEADB02"),
               vm("SITEADB03", mid="vm-a"), vm("siteadb03.other.example.mil", mid="vm-b"),
               vm("SITEAAPP77", guest="192.0.2.40")]
        fm = self.fm(rows, vms)
        n = by_name(fm)
        self.assertEqual(n["SITEADB01"][9], "1")                        # matched: powered on, heartbeat OK
        self.assertEqual(n["siteadb02.corp.example.mil"][9], "1")
        self.assertEqual(n["SITEADB03"][9], "")                         # two VMs, the IP settles nothing: not compared, said
        self.assertIn("a", n["SITEADB03"][12])
        self.assertEqual(fm["cmp"]["ambiguous"], 1)
        self.assertFalse([r for r in fm["nodes"] if "v" in r[12] and r[1].lower().startswith("siteadb03")])    # neither is "vCenter only"
        self.assertEqual(n["192.0.2.40"][9], "")                         # an IP caption is no name
        rows[2]["IPAddress"] = "192.0.2.98"
        vms[3]["ip"] = "192.0.2.98"
        n = by_name(self.fm(rows, vms))
        self.assertEqual((n["SITEADB03"][9], n["SITEADB03"][12]), ("1", ""))     # the IP settles it


class Review1(unittest.TestCase):
    """review round 1 (Codex): recovery needs known members, another domain or IP is another machine, an old Orion poll,
    the groups' completeness, every membership, absence only from a whole read, the since clock, the back-off clock"""

    def setUp(self):
        self.now = time.time()

    def test_unknown_never_clears_a_down_group(self):
        groups = {"list": [[1, "AD", 1]], "members": {"1": [1], "2": [1]}, "cut": False}
        fm = wf.model(snap([sw_row(1, "DC1", status=2), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
        self.assertEqual([c["id"] for c in wf.conds(fm, self.now)[0]], ["fleet:down:AD"])
        for st, um in ((0, False), (9, True), (1, True)):                 # unknown, unmanaged: no recovery seen
            fm = wf.model(snap([sw_row(1, "DC1", status=st, unmanaged=um), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
            cs, ok = wf.conds(fm, self.now)
            self.assertEqual((cs, "fleet:down:AD" in ok), ([], False), (st, um))
        fm = wf.model(snap([sw_row(1, "DC1"), sw_row(2, "DC2")], groups, self.now), None, OPTS, self.now)
        self.assertIn("fleet:down:AD", wf.conds(fm, self.now)[1])         # both known up: it clears

    def test_another_domain_or_ip_is_another_machine(self):
        rows = [sw_row(1, "APP", dns="app.prod.example.mil", ip="192.0.2.10", status=2)]
        for v in (vm("app.test.example.mil", power="poweredOff", hb=None), vm("APP", power="poweredOff", hb=None, ip="192.0.2.99")):
            fm = wf.model(snap(rows, now=self.now), view([v], self.now), OPTS, self.now)
            n = by_name(fm)["APP"]
            self.assertEqual(n[3], "d", v["name"])                       # never greyed by another machine's power state
            self.assertIn("c", n[12])
            self.assertEqual([r[1] for r in fm["nodes"] if "v" in r[12]], [v["name"]])     # that VM stays "vCenter only"
        fm = wf.model(snap(rows, now=self.now), view([vm("app.prod.example.mil", power="poweredOff", hb=None)], self.now), OPTS, self.now)
        self.assertEqual(by_name(fm)["APP"][3], "p")                      # the same FQDN, no IP to differ: the same machine

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
        self.assertFalse(wf.model(s2, None, wf.opts_of({"fleet_group_by": "custom:ServiceGroup"})[0], self.now)["complete"])

    def test_every_membership_counts(self):
        groups = {"list": [[1, "Alpha", 1], [2, "Beta", 1]], "members": {"1": [1, 2], "2": [2]}, "cut": False}
        fm = wf.model(snap([sw_row(1, "SHARED", status=2), sw_row(2, "B1")], groups, self.now), None, OPTS, self.now)
        self.assertEqual(sorted(c["id"] for c in wf.conds(fm, self.now)[0]), ["fleet:down:Alpha", "fleet:down:Beta"])
        self.assertEqual(sorted(w[0] for w in wf.summary(fm)["worst"]), ["Alpha", "Beta"])

    def test_absence_only_from_a_whole_current_read(self):
        vms = [vm("SITEAVMX01")]
        for sn in (snap([sw_row(1, "A1")], now=self.now, cut=True), dict(snap([], now=self.now), nodes=None, state="auth", at=None),
                   snap([sw_row(1, "A1")], now=self.now, age=3600)):
            fm = wf.model(sn, view(vms, self.now), wf.opts_of({"fleet_group_by": "custom:X"})[0], self.now)
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
            def paged(self2, *a, **k):
                t[0] += 600                                                 # the refusal after 10 min
                raise site_swis.SwisError("refused", "auth")
        r = wf.Reader(lambda: Slow([]), wf.opts_of({"fleet_group_by": "custom:X"})[0], clock=lambda: t[0], auth_retry=900)
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


class StaleNeverGreen(unittest.TestCase):
    """the area through the wall's own model (wall_model.build / summary): green only when SolarWinds is current, the read whole
    and nothing down"""

    def area(self, fm, now):
        m = wm.build(None, None, None, {}, wm.Tracker(), now, fleet=fm)
        s = wm.summary(m, now)
        return [a for a in s["areas"] if a["id"] == "fleet"][0], m

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
        self.assertFalse(any(x["id"] == "fleet" for x in m["areas"]))  # off: not on the wall at all

    def test_a_longer_interval_stretches_late(self):
        now = time.time()
        o = wf.opts_of({"fleet_seconds": 600})[0]
        a, m = self.area(wf.model(snap([sw_row(1, "A1")], now=now, age=900), None, o, now), now)
        self.assertEqual(a["state"], "ok")                              # read every 10 min: 15 min old is inside its own LATE

    def test_on_but_not_readable_says_why(self):
        now = time.time()
        m = wm.build(None, None, None, {}, wm.Tracker(), now, fleet=None, fleet_why="no SolarWinds login")
        a = [x for x in m["areas"] if x["id"] == "fleet"][0]
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
        self.assertEqual(len(fm["row"]), 18)
        codes = set(r[3] for r in fm["nodes"])
        self.assertTrue(codes <= set("uwdpmx"), codes)
        for r in fm["nodes"]:
            self.assertEqual(len(r), 18)
            self.assertIn(r[9], list(wf.VMW) + [""])
            self.assertTrue(-1 <= r[10] < len(fm["hosts"]))
            if r[3] == "u":
                self.assertEqual((r[8], r[16]), ("", ""))                # nothing said of an up node but its numbers
        self.assertEqual(fm["counts"]["d"], 5)
        self.assertEqual(fm["cmp"]["disagree_names"], ["SITEASQLV05"])
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
            return [m for m in self.groups["members"]]
        if "Orion.Container" in swql:
            return self.groups["list"]
        if "TOP 1" in swql:
            return self.rows[:1]
        return self.rows

    def paged(self, swql, order, params=None, page=1000, cap=None):
        rows = self.query("%s ORDER BY %s WITH ROWS 1 TO %d" % (swql, order, page), params)
        return rows, False


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
        o = wf.opts_of({"fleet_group_by": "custom:ServiceGroup"})[0]
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
        for o in (OPTS, wf.opts_of({"fleet_group_by": "custom:ServiceGroup"})[0]):
            sn = wf.Reader(f.client, o).read_once()
            self.assertEqual(sn["state"], "ok", sn["why"])
            self.assertEqual(len(sn["nodes"]), len(fx["nodes"]))
            now = time.time()
            fm = wf.model(sn, fd.view(now, fx), o, now)
            self.assertEqual(fm["counts"]["d"], 5)
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
        c = {"site": "SITEA", "stamp": "05:42", "day": "26 Nov", "zone": "Berlin", "src": "SolarWinds every 60 s", "link": "https://portal.example.mil/portal/wall?area=fleet", "stale": ""}
        o = self.run_js(L, c)
        self.assertEqual(o["line"][0], "DOWN  SITEADIRDCV01 - Active Directory - 192.0.2.12 - down 21 min (since 05:21)")
        self.assertEqual(o["line"][1], "WARN  SITEACERTV02 - Certificate Services - no IP - packet loss 8% (12 min, since 05:30)")
        self.assertTrue(o["text"].startswith("2 nodes not up (1 down) - SITEA - 05:42\nDOWN  SITEADIRDCV01"))
        self.assertTrue(o["text"].endswith("\nWall: https://portal.example.mil/portal/wall?area=fleet"))

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
        for k in ("portal_wall_fleet", "portal_wall_fleet_seconds", "portal_wall_fleet_group_by", "portal_wall_fleet_groups", "portal_wall_fleet_vm_match",
                  "portal_wall_fleet_max_nodes"):
            self.assertIn(k, d)
        t = open(os.path.join(ROOT, "roles", "site_portal_stack", "tasks", "main.yml")).read()
        for k in ("PORTAL_WALL_FLEET:", "fleet_seconds:", "fleet_group_by:", "fleet_groups:", "fleet_vm_match:", "fleet_max_nodes:"):
            self.assertIn(k, t)
        self.assertIn('_portal_has_swis: "{{ _portal_swis | bool }}"', t)                       # the login deployed for the Fleet area too
        self.assertIn("PORTAL_SWIS_URL: \"{{ portal_solarwinds_url | trim if _portal_swis | bool else '' }}\"", t)
        import site_portal_filters as F
        base = {"wall": True, "vc_on": True, "ontap_on": True, "netmap_sites": {}}
        self.assertEqual(F.site_portal_wall_summary(base), "Wall: VMware on, Storage on, Map places from netmap_sites: 0")
        self.assertEqual(F.site_portal_wall_summary(dict(base, fleet_on=True, fleet_group_by="custom:ServiceGroup")),
                         "Wall: VMware on, Storage on, Map places from netmap_sites: 0, Fleet on (by custom:ServiceGroup)")
        self.assertTrue(F.site_portal_wall_summary(dict(base, fleet_on=False, sw_url=False)).endswith("Fleet off (no SolarWinds address (SOLARWINDS_URL / netmap_solarwinds_url empty))"))

    def test_the_service_settings(self):
        import site_portal as sp
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        T = __import__("test_netapp_live")
        cfg = T.cfg_for(d, PORTAL_WALL_FLEET="1")
        self.assertTrue(cfg.wall_fleet)
        self.assertIn("no SolarWinds address", cfg.wall_fleet_why)
        cfg = T.cfg_for(d, PORTAL_WALL_FLEET="1", PORTAL_SWIS_URL="https://swis.example.invalid", PORTAL_SWIS_LOGIN=json.dumps({"user": "u", "password": "p"}),
                        PORTAL_WALL_OPTS=json.dumps({"fleet_group_by": "custom:Bad Name", "fleet_seconds": 120}))
        self.assertEqual((cfg.wall_fleet_why, cfg.wall_fleet_opts["seconds"], cfg.wall_fleet_opts["group_by"]), ("", 120, "groups"))
        self.assertTrue(cfg.wall_fleet_notes)
        cfg = T.cfg_for(d, PORTAL_WALL_FLEET="1", PORTAL_WALL="0")
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
            out = self.dom("area=fleet&style=%s&freeze=1" % st)
            self.assertIn('data-ready="1"', out, st)
            self.assertIn('class="wx wx-fleet"', out, st)
            self.assertIn("5 down", out, st)
            self.assertIn("SITEADIRDCV01", out, st)                     # the Not up list
            self.assertIn("Disagree: SolarWinds down, vCenter running with heartbeat", out, st)
            self.assertRegex(out, r"group \d+ of \d+ · problems first", st)    # the Spotlight's cue
            self.assertNotIn("<img src=\"x\"", out, st)                  # the markup in a caption stays text
        out = self.dom("style=strata&freeze=1")
        self.assertIn("Fleet &amp; services", out)                      # its tile in the room's overview
        self.assertNotIn("No live source on the wall yet (SolarWinds)", out)

    def test_card_and_bulk(self):
        out = self.dom("area=fleet&style=strata&fleet=one&freeze=1")
        self.assertIn('class="fl-ov on"', out)
        self.assertIn("Copy picture", out)
        self.assertIn("Copy both", out)
        self.assertIn("Save picture", out)
        self.assertIn("Add to selection", out)
        out = self.dom("area=fleet&style=metro&fleet=bulk&freeze=1")
        self.assertIn("Share 5 nodes to Teams", out)
        self.assertIn("5 selected", out)

    def test_stale_solarwinds_is_never_green(self):
        import demo
        url, stop = demo.portal(vc2_state="ok", fleet=fd.FakeReader(self.fx, age_s=900), collector=demo.FakeCollector(lambda: fd.view(fx=self.fx, vc_age=900)))
        self.addCleanup(stop)
        for st in ("strata", "metro", "cockpit"):
            out = self.dom("area=fleet&style=%s&freeze=1" % st, url=url)
            self.assertIn('data-ready="1"', out, st)
            board = out[out.index('class="wx wx-fleet"'):]
            self.assertNotIn('class="word ok"', board, st)               # no green word on the board: SolarWinds 15 min old
            self.assertIn("NOT CURRENT", board, st)
            self.assertIn("0 up", board, st)
            self.assertIn("no current data", board, st)


if __name__ == "__main__":
    unittest.main(verbosity=1)
