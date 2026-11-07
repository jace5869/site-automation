#!/usr/bin/env python3
"""Unit tests for the Horizon 8 jobs: plugins/filter/horizon_filters.py (every decision) and the pure parts of
roles/horizon/library/site_horizon.py (the login from the credential, Horizon's answers to bulk actions). The
module part needs ansible-core importable; the filters need the standard library only.
    python3 tests/test_horizon.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "horizon", "library"))
import horizon_filters as f  # noqa: E402

NOW = 1790000000000          # milliseconds
H = 3600 * 1000
D = 24 * H


def data(pools=(), machines=(), sessions=(), **raw):
    r = {"data": dict(desktop_pools=list(pools), machines=list(machines), sessions=list(sessions), **raw)}
    return f.hz_data(r)


IC = {"id": "p1", "name": "IC-Win11", "source": "INSTANT_CLONE", "type": "AUTOMATED", "vcenter_id": "vc1",
      "pattern_naming_settings": {"max_number_of_machines": 4, "number_of_spare_machines": 1}}
FC = {"id": "p2", "name": "FC-Old", "source": "FULL_CLONE", "type": "AUTOMATED", "pattern_naming_settings": {"max_number_of_machines": 2}}
PROT = {"id": "p3", "name": "VIP-Desk", "source": "INSTANT_CLONE", "vcenter_id": "vc1"}


def mach(i, pool, state, **kw):
    d = {"id": i, "name": "d-" + i, "desktop_pool_id": pool, "state": state}
    d.update(kw)
    return d


class Records(unittest.TestCase):
    def test_field_names_of_any_version(self):
        d = data([{"id": "p", "display_name": "Shown", "source": "instant_clone", "max_number_of_machines": "7",
                   "provisioning_status_data": {"instant_clone_pending_image_state": "PENDING"}}],
                 [{"id": "m", "dns_name": "x.corp.example.mil", "pool_id": "p", "state": "available", "user_id": "S-1-5-21-1"}],
                 [{"id": "s", "state": "disconnected", "disconnected_time": 1790000000}])   # seconds, not milliseconds
        p, m, s = d["pools"][0], d["machines"][0], d["sessions"][0]
        self.assertEqual((p["name"], p["source"], p["max"], p["pending_state"]), ("Shown", "INSTANT_CLONE", 7, "PENDING"))
        self.assertEqual((m["name"], m["pool_id"], m["state"], m["users"]), ("x.corp.example.mil", "p", "AVAILABLE", ["S-1-5-21-1"]))
        self.assertEqual((s["state"], s["disconnected_ms"]), ("DISCONNECTED", NOW))
        self.assertEqual(f._g({"a": {"b": ""}, "c": 0}, "a.b", "c"), 0)
        self.assertEqual(f._list("a, b\nc"), ["a", "b", "c"])


class Desktops(unittest.TestCase):
    def test_fix_and_held(self):
        d = data([IC, FC, PROT],
                 [mach("1", "p1", "AGENT_UNREACHABLE"), mach("2", "p1", "AGENT_ERR_NEED_REBOOT"), mach("3", "p1", "AVAILABLE"),
                  mach("4", "p1", "ERROR", session_id="s9"), mach("5", "p2", "ERROR"), mach("6", "p3", "ALREADY_USED"),
                  mach("7", "p1", "PROVISIONING_ERROR", user_ids=["S-1-5-21-1"]), mach("8", "p1", "PROVISIONING"),
                  mach("9", "px", "ALREADY_USED")],
                 [{"id": "s1", "machine_id": "1", "session_state": "PENDING"}])
        r = f.hz_desktops(d, {"protected_pools": ["vip-*"], "actions": {"agent_err_need_reboot": "restart", "PROVISIONING_ERROR": "remove"}})
        fix = dict((x["id"], x["action"]) for x in r["fix"])
        held = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertEqual(fix, {"2": "restart"})
        self.assertIn("pool px is not among the pools Horizon gave", held["9"])      # never act on a pool not read
        self.assertIn("a user's session is on it (pending)", held["1"])
        self.assertIn("a user's session is on it (open)", held["4"])
        self.assertIn("pool FC-Old is full clone", held["5"])
        self.assertIn("pool VIP-Desk is protected", held["6"])
        self.assertIn("remove would take their desktop away", held["7"])
        self.assertNotIn("8", fix)
        self.assertEqual(r["by_state"]["ERROR"], 2)
        self.assertEqual(r["report"]["status"], "critical")

    def test_cap_and_unknown_action(self):
        d = data([IC], [mach(str(i), "p1", "ALREADY_USED") for i in range(5)])
        r = f.hz_desktops(d, {"max": 2})
        self.assertEqual((len(r["fix"]), len(r["held"])), (2, 3))
        self.assertIn("over horizon_fix_max (2 per run)", r["held"][0]["reason"])
        r = f.hz_desktops(d, {"default_action": "reboot"})
        self.assertIn("its action reboot is not one of", r["held"][0]["reason"])
        self.assertEqual(f.hz_desktops(data([IC], [mach("1", "p1", "AVAILABLE")]))["report"]["title"], "Horizon desktops: none broken")

    def test_recheck(self):
        ok = {"desktops": [{"id": "1", "name": "d-1", "pool_id": "p1", "action": "recover", "state": "AGENT_UNREACHABLE"},
                           {"id": "2", "name": "d-2", "pool_id": "p1", "action": "recover"},
                           {"id": "3", "name": "d-3", "pool_id": "p1", "action": "recover"},
                           {"id": "4", "name": "d-4", "pool_id": "p1", "action": "rm -rf"},
                           {"id": "9", "name": "gone", "pool_id": "p1", "action": "recover"}]}
        d = data([IC], [mach("1", "p1", "ERROR"), mach("2", "p1", "AVAILABLE"), mach("3", "p1", "ERROR", session_id="s"),
                        mach("4", "p1", "ERROR")])
        r = f.hz_desktops_recheck(ok, d, {})
        self.assertEqual([x["id"] for x in r["act"]], ["1"])
        self.assertEqual(r["act"][0]["state"], "ERROR")
        why = dict((x["id"], x["reason"]) for x in r["skipped"])
        self.assertEqual(why["2"], "it is AVAILABLE now: it recovered by itself")
        self.assertEqual(why["3"], "a user's session is on it now")
        self.assertIn("not one the job runs", why["4"])
        self.assertEqual(why["9"], "no longer in Horizon")
        self.assertEqual(f.hz_desktops_recheck({}, d, {}), {"act": [], "skipped": []})
        # every check of the check job again, on what Horizon says NOW - not on the approved record
        ok = {"desktops": [{"id": "5", "name": "d-5", "pool_id": "p1", "pool": "IC-Win11", "action": "remove"},      # assigned since
                           {"id": "6", "name": "d-6", "pool_id": "p1", "pool": "IC-Win11", "action": "recover"},     # moved to full clones
                           {"id": "7", "name": "d-7", "pool_id": "p1", "pool": "IC-Win11", "action": "recover"},     # excluded since
                           {"id": "8", "name": "d-8", "pool_id": "p1", "pool": "IC-Win11", "action": "recover"},     # moved to a protected pool
                           {"id": "10", "name": "d-10", "pool_id": "p1", "pool": "IC-Win11", "action": "recover"},   # its pool was not read
                           {"id": "11", "name": "d-11", "pool_id": "p1", "pool": "IC-Win11", "action": "remove"}]}   # still fine
        d = data([IC, FC, PROT], [mach("5", "p1", "ERROR", user_ids=["S-1-5-21-1"]), mach("6", "p2", "ERROR"), mach("7", "p1", "ERROR"),
                                  mach("8", "p3", "ERROR"), mach("10", "px", "ERROR"), mach("11", "p1", "ERROR")])
        r = f.hz_desktops_recheck(ok, d, {"protected_pools": ["VIP-*"], "exclude_machines": ["d-7"]})
        self.assertEqual([x["id"] for x in r["act"]], ["11"])
        why = dict((x["id"], x["reason"]) for x in r["skipped"])
        self.assertIn("assigned to a user now", why["5"])
        self.assertIn("recover is for instant clones, and pool FC-Old is not", why["6"])
        self.assertIn("excluded (horizon_fix_exclude_machines)", why["7"])
        self.assertIn("pool VIP-Desk is protected", why["8"])
        self.assertIn("pool px is not among the pools Horizon gave", why["10"])


class Sessions(unittest.TestCase):
    def test_old_ones(self):
        ss = [{"id": "a", "user_id": "S-1-5-21-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 9 * H},
              {"id": "b", "user_id": "S-2", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 2 * H},
              {"id": "c", "user_id": "S-3", "machine_id": "", "farm_id": "f1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 30 * H},
              {"id": "d", "user_id": "S-4", "desktop_pool_id": "p3", "session_state": "DISCONNECTED", "disconnected_time": NOW - 30 * H},
              {"id": "e", "user_id": "S-5", "desktop_pool_id": "p1", "session_state": "DISCONNECTED"},
              {"id": "g", "user_id": "S-6", "desktop_pool_id": "p1", "session_state": "CONNECTED"},
              {"id": "h", "user_id": "S-10", "desktop_pool_id": "px", "session_state": "DISCONNECTED", "disconnected_time": NOW - 30 * H}]
        d = data([IC, PROT], [mach("1", "p1", "DISCONNECTED")], ss, farms=[{"id": "f1", "name": "Farm A"}])
        r = f.hz_sessions_old(d, {"now": NOW, "hours": 8, "protected_pools": ["VIP-*"], "names": {"S-1-5-21-1": "CORP\\jdoe"},
                                  "exclude_users": []})
        self.assertEqual([x["id"] for x in r["logoff"]], ["c", "a"])          # the oldest first
        self.assertEqual(r["logoff"][1]["label"], "CORP\\jdoe on d-1")
        self.assertEqual((r["logoff"][0]["machine"], r["logoff"][0]["pool"]), ("(an RDS host)", "farm Farm A"))
        held = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertIn("pool VIP-Desk is excluded", held["d"])
        self.assertEqual(held["e"], "when it was disconnected is not known")
        self.assertIn("pool px is not among the pools Horizon gave", held["h"])
        self.assertEqual(r["recent"], 1)
        # a user whose name could not be read is left alone only while exclude_users names users (not SIDs only)
        r = f.hz_sessions_old(d, {"now": NOW, "hours": 8, "exclude_users": ["jdoe"], "unnamed": ["S-3"]})
        self.assertIn("name could not be read", dict((x["id"], x["reason"]) for x in r["held"])["c"])
        self.assertEqual(f.hz_sessions_old(d, {"now": NOW, "hours": 8, "exclude_users": ["S-1-5-21-9"], "unnamed": ["S-3"]})["logoff"][0]["id"], "c")
        r = f.hz_sessions_old(d, {"now": NOW, "hours": 8, "names": {"S-1-5-21-1": "CORP\\jdoe"}, "exclude_users": "jdoe, S-3", "max": 5})
        self.assertEqual(r["logoff"][0]["id"], "d")
        self.assertIn("the user is excluded", dict((x["id"], x["reason"]) for x in r["held"])["a"])
        self.assertEqual(len(f.hz_sessions_old(d, {"now": NOW, "max": 1})["logoff"]), 1)


USERS = [{"id": "S-1-5-21-1", "login_name": "jdoe", "display_name": "Jane Doe", "domain": "CORP", "user_principal_name": "jdoe@corp.example.mil"},
         {"id": "S-2", "login_name": "sam", "domain": "CORP"}, {"id": "S-3", "login_name": "sam", "domain": "LAB"},
         {"id": "S-9", "login_name": "jdoe", "domain": "CORP", "group": True}]


class Users(unittest.TestCase):
    def setUp(self):
        self.d = data([IC, FC], [mach("1", "p1", "CONNECTED", session_id="s1"), mach("2", "p2", "ERROR", user_ids=["S-1-5-21-1"]),
                                 mach("3", "p1", "CONNECTED")],
                      [{"id": "s1", "user_id": "S-1-5-21-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "CONNECTED"},
                       {"id": "s2", "user_id": "S-1-5-21-1", "machine_id": "3", "desktop_pool_id": "p1", "session_state": "DISCONNECTED",
                        "disconnected_time": NOW},
                       {"id": "s3", "user_id": "S-7", "machine_id": "3", "desktop_pool_id": "p1", "session_state": "CONNECTED"},
                       {"id": "s4", "user_id": "S-8", "user_name": "CORP\\nora", "farm_id": "f", "session_state": "CONNECTED"}],
                      ad_users=USERS)

    def test_find(self):
        for who in ("jdoe", "CORP\\jdoe", "jdoe@corp.example.mil", "S-1-5-21-1"):
            u = f.hz_user(self.d, who)
            self.assertEqual((u["sid"], u["problems"]), ("S-1-5-21-1", []), who)
        u = f.hz_user(self.d, "jdoe")
        self.assertEqual(f.hz_user_label(u), "CORP\\jdoe (Jane Doe), S-1-5-21-1")
        self.assertEqual(sorted(s["id"] for s in u["sessions"]), ["s1", "s2"])
        self.assertEqual(sorted((m["name"], m["assigned"], m["others"]) for m in u["machines"]),
                         [("d-1", False, 0), ("d-2", True, 0), ("d-3", False, 1)])
        self.assertIn("2 users are sam (CORP\\sam, LAB\\sam)", f.hz_user(self.d, "sam")["problems"][0])
        self.assertEqual(f.hz_user(self.d, "LAB\\sam")["sid"], "S-3")
        self.assertEqual(f.hz_user(self.d, "sam@lab.example.mil")["sid"], "S-3")
        u = f.hz_user(self.d, "LAB\\jdoe")                    # the domain counts: never CORP's jdoe
        self.assertEqual((u["sid"], u["sessions"], u["machines"]), ("", [], []))
        self.assertIn("No user jdoe in domain LAB - Horizon knows CORP\\jdoe", u["problems"][0])
        u = f.hz_user(self.d, "nora")                         # no AD record: the name a session carries
        self.assertEqual(([s["id"] for s in u["sessions"]], u["problems"]), (["s4"], []))
        self.assertIn("Horizon knows no user LAB\\nora", f.hz_user(self.d, "LAB\\nora")["problems"][0])
        self.assertIn("Horizon knows no user ghost", f.hz_user(self.d, "ghost")["problems"][0])
        raw = dict((k, v) for k, v in self.d["raw"].items() if k != "ad_users")   # as user_lookup.yml reads: two AD searches
        down = dict(self.d, raw=dict(raw, **{"ad_users:login": [], "ad_users:name": []}),
                    errors={"ad_users:login": "HTTP 500", "ad_users:name": "HTTP 500"})
        u = f.hz_user(down, "nora")                           # the AD search failed: no guess by a session's name
        self.assertEqual(u["sessions"], [])
        self.assertIn("AD user search did not answer (HTTP 500; HTTP 500)", u["problems"][0])
        down["errors"] = {"ad_users:login": "HTTP 400"}       # one of the two answered: as before
        self.assertEqual(f.hz_user(down, "nora")["problems"], [])
        self.assertIn("No user given", f.hz_user(self.d, " ")["problems"][0])
        self.assertIn("BROKEN", f.hz_user_machine_line(u and f.hz_user(self.d, "jdoe")["machines"][1]))

    def test_targets(self):
        u = f.hz_user(self.d, "jdoe")
        t = f.hz_user_targets(u, "logoff", self.d)
        self.assertEqual((t["target"], sorted(t["ids"])), ("sessions", ["s1", "s2"]))
        t = f.hz_user_targets(u, "disconnect", self.d)
        self.assertEqual((t["ids"], t["held"][0]["reason"]), (["s1"], "it is disconnected, not connected"))
        t = f.hz_user_targets(u, "recover", self.d, {})
        self.assertEqual(t["ids"], ["1"])
        why = dict((x["name"], x["reason"]) for x in t["held"])
        self.assertEqual(why["d-3"], "another user has a session on it")
        self.assertIn("recover is for instant clones; pool FC-Old is not", why["d-2"])
        self.assertEqual(sorted(f.hz_user_targets(u, "reset", self.d)["ids"]), ["1", "2"])
        self.assertIn("pool IC-Win11 is protected", f.hz_user_targets(u, "reset", self.d, {"protected_pools": ["ic-*"]})["held"][0]["reason"])
        t = f.hz_user_targets(u, "logoff", self.d, {"protected_pools": ["ic-*"]})      # sessions in a protected pool: left alone too
        self.assertEqual(t["ids"], [])
        self.assertEqual(sorted(x["name"] for x in t["held"]), ["connected session on d-1", "disconnected session on d-3"])
        self.assertIn("pool IC-Win11 is protected", t["held"][0]["reason"])
        self.assertEqual(f.hz_user_targets(u, "disconnect", self.d, {"protected_pools": ["ic-*"]})["ids"], [])
        unread = data([FC], self.d["raw"]["machines"], self.d["raw"]["sessions"], ad_users=USERS)   # p1 was not read
        t = f.hz_user_targets(f.hz_user(unread, "jdoe"), "logoff", unread)
        self.assertEqual((t["ids"], len(t["held"])), ([], 2))
        self.assertIn("pool p1 is not among the pools Horizon gave", t["held"][0]["reason"])
        self.assertEqual(f.hz_user_targets(u, "look", self.d)["ids"], [])
        rds = f.hz_user_targets(f.hz_user(self.d, "nora"), "reset", self.d)
        self.assertIn("RDS hosts: log them off instead", rds["held"][0]["reason"])


class Push(unittest.TestCase):
    def test_plan(self):
        pend = dict(IC, id="p4", name="IC-Busy", provisioning_status_data={"instant_clone_operation": "PUSH_IMAGE"})
        d = data([IC, FC, PROT, pend])
        pp = f.hz_push_pools(d, "IC-Win11, FC-Old, VIP-Desk, IC-Busy, Nope, ic-win11", {"protected_pools": ["VIP-*"]})
        self.assertEqual([p["id"] for p in pp["pools"]], ["p1"])
        e = " | ".join(pp["problems"])
        for want in ("pool FC-Old is not instant clones (full clone)", "pool VIP-Desk is protected", "pool IC-Busy already has a push",
                     "no desktop pool Nope"):
            self.assertIn(want, e)
        vms = [{"id": "v1", "name": "Gold", "path": "/dc/Gold"}, {"id": "v2", "name": "gold", "path": "/dc2/gold", "incompatible_reasons": ["X"]}]
        self.assertEqual(f.hz_pick(vms, "Gold")["id"], "v1")              # the exact name first
        self.assertIn("Horizon will not push golden image VM gold: X", f.hz_pick(vms, "/dc2/gold")["problem"])
        self.assertIn("2 golden image VMs are named GOLD", f.hz_pick(vms, "GOLD")["problem"])
        self.assertIn("no snapshot named s (Horizon lists none)", f.hz_pick([], "s", "snapshot")["problem"])
        start = f.hz_push_start("", NOW)
        plan = f.hz_push_plan(pp, {"vc1": f.hz_pick(vms, "Gold")}, {"vc1": {"id": "sn", "name": "s1", "path": "/s1", "warnings": []}}, start,
                              {"logoff_policy": "force_logoff"})
        self.assertEqual(plan["pools"], [{"id": "p1", "name": "IC-Win11", "vcenter_id": "vc1", "parent_vm_id": "v1", "parent_vm": "Gold",
                                          "snapshot_id": "sn", "snapshot": "/s1"}])
        self.assertIn("LOGGED OFF (FORCE_LOGOFF)", plan["lines"][0])
        self.assertEqual(plan["logoff_policy"], "FORCE_LOGOFF")
        self.assertIn("is in the past", f.hz_push_start("2020-01-01 00:00", NOW)["problem"])
        self.assertIn("is not a time", f.hz_push_start("tonight", NOW)["problem"])
        self.assertEqual(f.hz_push_start("2026-09-21T22:00-05:00", NOW)["text"], "2026-09-22 03:00 UTC")
        self.assertEqual(f.hz_push_body({"start_ms": 0}, NOW)["start_time"], NOW + 60000)   # now: in a minute
        self.assertEqual(f.hz_push_body({"start_ms": NOW + D}, NOW)["start_time"], NOW + D)
        r = f.hz_push_recheck(plan, data([pend, dict(IC, provisioning_status_data={"instant_clone_pending_image_state": "PENDING"})]), {})
        self.assertIn("a push is already scheduled", r["skipped"][0]["reason"])
        self.assertEqual(f.hz_push_recheck(plan, data([IC]), {})["push"][0]["id"], "p1")


class Reports(unittest.TestCase):
    def test_health(self):
        d = data([dict(IC, provisioning_status_data={"instant_clone_current_image_state": "FAILED"})], [mach("1", "p1", "ERROR")], [],
                 connection_servers=[{"name": "cs1", "status": "OK", "certificate": {"valid": True, "valid_to": NOW + 20 * D},
                                      "services": [{"service_name": "Blast", "status": "DOWN"}]},
                                     {"name": "cs2", "status": "NOT_RESPONDING", "certificate": {"valid": False, "invalid_reason": "untrusted"}}],
                 gateways=[{"name": "uag", "status": "STALE"}], event_database=[{"status": "NOT_CONFIGURED"}],
                 license=[{"licensed": True, "expiration_time": NOW - D}],
                 ad_domains=[{"dns_name": "corp.example.mil", "connection_servers": [{"name": "cs1", "status": "CANNOT_BIND"}]}])
        d["errors"] = {"true_sso": "not in this Horizon (HTTP 404 at every API version, v9 to v1)", "gateways": "x answered HTTP 500"}
        r = f.hz_health(d, {"now": NOW})
        got = dict(((p["what"], p["text"]), p["severity"]) for p in r["problems"])
        self.assertEqual(got[("cs1", "its certificate expires in 20 days (%s)" % f._when(NOW + 20 * D))], "warning")
        self.assertEqual(got[("cs1", "service Blast is DOWN")], "critical")
        self.assertEqual(got[("cs2", "status NOT_RESPONDING")], "critical")
        self.assertEqual(got[("cs2", "its certificate is not valid (untrusted)")], "critical")
        self.assertEqual(got[("uag", "status STALE")], "warning")
        self.assertEqual(got[("-", "no event database: Horizon keeps no history of its events")], "warning")
        self.assertEqual(got[("-", "the license expired %s" % f._when(NOW - D))], "critical")
        self.assertEqual(got[("IC-Win11", "its current image is failed")], "critical")
        self.assertEqual(got[("corp.example.mil", "from Connection Server cs1: CANNOT_BIND")], "warning")
        self.assertEqual(got[("true_sso", "not in this Horizon (HTTP 404 at every API version, v9 to v1)")], "info")
        self.assertEqual(got[("gateways", "x answered HTTP 500")], "warning")
        self.assertEqual(r["report"]["status"], "critical")
        self.assertEqual(r["problems"][0]["severity"], "critical")       # problems first
        self.assertEqual(f.hz_health(data([], connection_servers=[{"name": "a", "status": "OK"}]), {"now": NOW})["report"]["title"],
                         "Horizon health report: healthy")
        self.assertIn("Horizon listed no Connection Server", str(f.hz_health(data([]), {})["problems"]))

    def test_capacity(self):
        full = dict(IC, id="pf", name="Full")
        spare = dict(IC, id="ps", name="Spare", pattern_naming_settings={"max_number_of_machines": 10, "number_of_spare_machines": 3})
        d = data([full, spare, {"id": "r", "name": "RDS", "type": "RDS"}],
                 [mach(str(i), "pf", "CONNECTED") for i in range(4)] + [mach("s1", "ps", "AVAILABLE"), mach("s2", "ps", "DISCONNECTED")])
        r = f.hz_capacity(d, {"warn_pct": 80, "crit_pct": 95})
        st = dict((p["name"], (p["status"], p["why"])) for p in r["pools"])
        self.assertEqual(st["Full"], ("critical", "full: no desktop left for the next user"))
        self.assertEqual(st["Spare"], ("warning", "fewer free desktops (1) than its spares (3)"))
        self.assertEqual(st["RDS"][0], "info")
        self.assertEqual((r["critical"], r["warning"]), (1, 1))

    def test_lines(self):
        self.assertEqual(f.hz_test_lines([{"name": "machines", "path": "rest/inventory/v3/machines", "status": "ok", "count": 2, "more": True},
                                          {"name": "license", "status": "missing", "error": "not in this Horizon"},
                                          {"name": "sessions", "status": "error", "error": "HTTP 403"}]),
                         ["PASS machines: rest/inventory/v3/machines - 2 record(s) and more", "----  license: not in this Horizon",
                          "FAIL sessions: HTTP 403"])
        self.assertEqual(f.hz_act_lines([{"id": "1", "ok": True}, {"id": "2", "ok": False, "error": "busy"}],
                                        [{"id": "1", "name": "a"}, {"id": "2", "label": "u on b"}], "reset"),
                         ["a: reset requested - Horizon took it", "u on b: REFUSED - busy"])
        self.assertEqual(f.hz_act_lines([{"id": "1", "status": "would"}], [], "reset"), ["1: would reset (a dry run)"])
        sc = f.hz_settings_check(data([IC]), {"horizon_protected_pools": ["IC-*", "nope"], "horizon_push_pools": ["x"],
                                             "horizon_broken_states": ["ERROR", "ERORR"], "horizon_fix_actions": {"ERROR": "zap"}})
        self.assertEqual(len(sc["errors"]), 2)
        self.assertIn("horizon_protected_pools: IC-* matches IC-Win11", sc["lines"])
        self.assertIn("ERORR is not a desktop state", " ".join(sc["warnings"]))


class Gaps(unittest.TestCase):
    """0.83.0: what the desktops check holds back now, the health report's new findings, the events and settings reports."""

    def mmd(self, i, state, **mdd):
        return mach(i, "p1", state, managed_machine_data=mdd)

    def test_clone_error_kinds(self):
        for msg, kind in (("The domain join failed", "domain"), ("Trust relationship failed", "domain"),
                          ("No IP address: DHCP", "network"), ("got 169.254.3.4", "network"),
                          ("Insufficient disk space on datastore DS2", "datastore"), ("Windows activation failed (KMS)", "activation"),
                          ("Customization operation timed out", "timeout"), ("something else", "other"), ("", "")):
            self.assertEqual(f._classify(msg), kind, msg)
        self.assertEqual(f._classify("Carbon Black removed it", {"security_agent": "carbon black"}), "security_agent")
        self.assertEqual(f._classify("x", {"bad": "("}), "other")            # a broken pattern is skipped, never an error

    def test_holds(self):
        d = data([IC], [self.mmd("1", "PROVISIONING_ERROR", clone_error_message="Domain join failed"),
                        self.mmd("2", "PROVISIONING_ERROR", clone_error_message="Customization operation timed out"),
                        self.mmd("3", "ERROR", missing_in_vcenter=True),
                        self.mmd("4", "ERROR", missing_in_vcenter=True, host_name="esx9"),
                        self.mmd("5", "CUSTOMIZING", create_time=NOW - 9 * H), self.mmd("6", "CUSTOMIZING", create_time=NOW - 1 * H),
                        self.mmd("7", "DELETING", last_maintenance_time=NOW - 30 * H, create_time=NOW - 99 * H),
                        self.mmd("8", "PROVISIONING")]
                 + [self.mmd("h%d" % n, "AGENT_UNREACHABLE", host_name="esx9") for n in range(3)])
        r = f.hz_desktops(d, {"now": NOW, "host_threshold": 3})
        fix = dict((x["id"], x["action"]) for x in r["fix"])
        why = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertEqual(fix, {"2": "recover", "3": "remove", "4": "remove"})        # a ghost on a failed host is still removed
        self.assertIn("domain problem", why["1"])
        self.assertIn("stuck customizing since", why["5"])
        self.assertIn("stuck deleting", why["7"])
        self.assertNotIn("6", why)                                                 # 1 hour: still being made
        self.assertNotIn("8", why)                                                 # no time given: not counted
        self.assertIn("4 desktops on ESXi host esx9 are broken at once", why["h0"])   # the ghost there too
        self.assertEqual(r["gates"]["hosts"], {"esx9": 4})
        r = f.hz_desktops(d, {"now": NOW, "host_threshold": 0, "hold_error_kinds": [], "ghost_action": "",
                              "stuck_actions": {"customizing": "recover"}})
        fix = dict((x["id"], x["action"]) for x in r["fix"])
        why = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertEqual(fix.get("1"), "recover")                                 # [] = recover every kind
        self.assertEqual(fix.get("5"), "recover")
        self.assertEqual(fix.get("h0"), "recover")                                # 0 = no host check
        self.assertIn("vCenter has no such VM any more", why["3"])                # "" = left for a person

    def test_ad_gate(self):
        ad = [{"dns_name": "corp.example.mil", "connection_servers": [{"name": "cs1", "status": "FULLY_ACCESSIBLE"},
                                                                       {"name": "cs2", "status": "CANNOT_BIND"}]}]
        d = data([IC], [mach("1", "p1", "AGENT_UNREACHABLE"), mach("2", "p1", "AGENT_ERR_NEED_REBOOT")], ad_domains=ad)
        r = f.hz_desktops(d, {"actions": {"AGENT_ERR_NEED_REBOOT": "restart"}})
        self.assertEqual([x["id"] for x in r["fix"]], ["2"])                      # a restart makes no new clone
        self.assertIn("cs2: CANNOT_BIND: a new clone could not join its domain", r["held"][0]["reason"])
        self.assertEqual([x["id"] for x in f.hz_desktops(d, {"ad_gate": False})["fix"]], ["2", "1"])
        # after the approval: AD down NOW holds the approved recover
        ok = {"desktops": [{"id": "1", "action": "recover"}, {"id": "2", "action": "restart"}]}
        rc = f.hz_desktops_recheck(ok, d, {})
        self.assertEqual([x["id"] for x in rc["act"]], ["2"])
        self.assertIn("CANNOT_BIND", rc["skipped"][0]["reason"])

    def test_recheck_kinds(self):
        d = data([IC], [self.mmd("3", "ERROR", missing_in_vcenter=True), self.mmd("4", "ERROR"),
                        self.mmd("5", "CUSTOMIZING", create_time=NOW - 9 * H), self.mmd("6", "AVAILABLE", create_time=NOW - 9 * H)])
        ok = {"desktops": [{"id": "3", "action": "remove", "ghost": True}, {"id": "4", "action": "remove", "ghost": True},
                           {"id": "5", "action": "recover", "stuck": True, "state": "CUSTOMIZING"},
                           {"id": "6", "action": "recover", "stuck": True, "state": "CUSTOMIZING"}]}
        rc = f.hz_desktops_recheck(ok, d, {"now": NOW})
        self.assertEqual([x["id"] for x in rc["act"]], ["3", "5"])
        why = dict((x["id"], x["reason"]) for x in rc["skipped"])
        self.assertEqual(why["4"], "vCenter has it again")
        self.assertIn("AVAILABLE now", why["6"])

    def test_health_additions(self):
        raw = {"connection_servers": [{"name": "cs1", "status": "OK", "default_certificate": True}],
               "virtual_centers": [{"name": "vc", "datastores": [{"id": "d1", "details": {"name": "DS1", "capacity_mb": 100000}, "free_space_mb": 3000},
                                                                  {"id": "d2", "details": {"name": "DS2", "capacity_mb": 100000}, "free_space_mb": 10000}],
                                    "hosts": [{"details": {"name": "esx1"}, "status": "NOT_RESPONDING"}]}],
               "cs_config": [{"id": "1", "name": "cs1"}],
               "cs_config:1": [{"id": "1", "name": "cs1", "ldap_backup_info": {"last_ldap_backup_time": NOW - 40 * H, "last_ldap_backup_status": "OK"}}]}
        pools = [dict(IC, provisioning_settings={"base_snapshot_id": "s2"}, provisioning_status_data={"instant_clone_pending_image_state": "READY_HELD"}),
                 dict(IC, id="p9", name="IC-Two", provisioning_settings={"base_snapshot_id": "s2"})]
        ms = [mach("1", "p9", "AVAILABLE", agent_version="8.12", agent_ipv4_address="169.254.1.1", managed_machine_data={"base_vm_snapshot_id": "s1"}),
              mach("2", "p9", "AVAILABLE", agent_version="8.11", agent_ipv4_address="192.0.2.5"),
              mach("3", "p9", "CONNECTED", agent_version="8.12", agent_ipv4_address="192.0.2.5"),
              mach("4", "p9", "BLOCKED_AGENT_VERSION")]
        h = f.hz_health(data(pools, ms, **raw), {"now": NOW})
        t = " | ".join("%s %s" % (p["severity"], p["text"]) for p in h["problems"])
        for want in ("warning it serves its own self-signed certificate", "critical 3 GB free of 98 GB (3%)", "warning 10 GB free of 98 GB (10%)",
                     "warning NOT_RESPONDING (vCenter vc)", "warning its last configuration backup was 40 hours ago",
                     "warning a new image is ready but held", "warning Horizon Agent of 2 versions: 8.12 (2), 8.11 (1)",
                     "blocked for their agent version: d-4", "not in use still on an older image after the push: d-1",
                     "1 with a 169.254 address", "the same IP address on two or more desktops: 192.0.2.5 (d-2, d-3)"):
            self.assertIn(want, t)
        self.assertEqual(h["report"]["sections"][-1]["title"], "Datastores")
        # the push plan: vGPU and space
        pp = f.hz_push_pools(data([dict(IC, storage_settings={"datastores": [{"datastore_id": "d1"}]})]), "IC-Win11")
        plan = f.hz_push_plan(pp, {"vc1": {"id": "v", "name": "G", "warnings": []}},
                              {"vc1": {"id": "s", "name": "s", "path": "/s", "warnings": [], "disk_mb": 40960, "vgpu": "grid_a40-2q"}},
                              f.hz_push_start("", NOW), {"datastores": f.hz_datastores(data(**raw))})
        w = " | ".join(plan["warnings"])
        self.assertIn("vGPU profile (grid_a40-2q) is not the pool's (none)", w)
        self.assertIn("datastore DS1 has 3 GB free; the new image's replica needs about 40 GB", w)

    def test_events(self):
        ev = [{"type": "BROKER_USER_AUTHFAILED_BAD_USER_PASSWORD", "severity": "AUDIT_FAIL", "time": NOW - n * H,
               "message": "User CORP\\jdoe has failed to authenticate to Horizon from 198.51.100.23"} for n in range(1, 6)]
        ev += [{"type": "BROKER_USER_NOT_ENTITLED", "severity": "AUDIT_FAIL", "time": NOW - H, "user_id": "S-1-5-21-7", "message": "no user here"},
               {"type": "BROKER_POOL_EMPTY", "severity": "ERROR", "time": NOW - H, "desktop_pool_id": "p1", "message": "Pool IC-Win11 is empty"},
               {"type": "VLSI_X", "severity": "AUDIT_SUCCESS", "module": "Vlsi", "time": NOW - H, "message": "User CORP\\adm changed it"},
               {"type": "BROKER_POOL_EMPTY", "severity": "ERROR", "time": NOW - 30 * H, "message": "old"}]
        r = f.hz_events(data([IC], audit_events=ev), {"now": NOW, "hours": 24})
        self.assertEqual((r["failed"], r["errors"], r["admin"]), (6, 1, 1))
        self.assertEqual(r["users"], {"CORP\\jdoe": 5, "S-1-5-21-7": 1})
        self.assertEqual(r["warning"], 2)
        rows = r["report"]["sections"][1]["rows"]
        self.assertEqual(rows[0][:4], ["CORP\\jdoe", 5, "bad user password (5)", "198.51.100.23"])
        self.assertEqual(r["report"]["sections"][2]["rows"][0][:3], ["BROKER_POOL_EMPTY", 1, "IC-Win11"])
        q = __import__("json").loads(f.hz_events_filter(24, NOW))
        self.assertEqual((q["name"], q["toValue"] - q["fromValue"]), ("time", 24 * H))
        r = f.hz_events({"raw": {}, "errors": {"audit_events": "HTTP 500"}}, {"now": NOW})
        self.assertEqual(r["critical"], 1)

    def test_settings(self):
        raw = {"settings_general": [{"client_idle_session_timeout_policy": "NEVER", "client_max_session_timeout_minutes": 600,
                                     "pre_login_message": "You are accessing a U.S. Government (USG) system", "nested": {"a": True}}],
               "cs_config": [{"name": "cs1", "fips_mode_enabled": True, "smart_card_support": "OPTIONAL", "ldap_backup_info": {"x": 1}},
                             {"name": "cs2", "fips_mode_enabled": False, "smart_card_support": "REQUIRED"}]}
        base = {"general.client_idle_session_timeout_policy": "timeout_after", "general.client_max_session_timeout_minutes": {"max": 720},
                "general.pre_login_message": {"contains": "u.s. government"}, "general.nested.a": True,
                "connection_server.fips_mode_enabled": True, "connection_server.smart_card_support": {"one_of": ["REQUIRED"]},
                "security.x": 1}
        r = f.hz_settings(data(**raw), base)
        res = dict(("%s@%s" % (x[0], x[1]), x[4]) for x in r["report"]["sections"][0]["rows"])
        self.assertEqual(res["general.client_idle_session_timeout_policy@global"], "DRIFT")
        self.assertEqual(res["general.client_max_session_timeout_minutes@global"], "OK")
        self.assertEqual(res["general.pre_login_message@global"], "OK")
        self.assertEqual(res["general.nested.a@global"], "OK")
        self.assertEqual((res["connection_server.fips_mode_enabled@cs1"], res["connection_server.fips_mode_enabled@cs2"]), ("OK", "DRIFT"))
        self.assertEqual(res["connection_server.smart_card_support@cs1"], "DRIFT")
        self.assertEqual(res["security.x@global"], "NOT READ")
        self.assertEqual(r["drift"], 3)
        every = [x[0] for x in r["report"]["sections"][-1]["rows"]]
        self.assertNotIn("connection_server.ldap_backup_info.x", every)
        self.assertEqual(f.hz_settings(data(**raw), {})["report"]["title"], "Horizon settings: no baseline set")
        self.assertEqual(f._expect("5", {"min": 1, "max": 10}), (True, ">= 1 and <= 10"))
        self.assertEqual(f._expect("abc", {"not": "ABC"})[0], False)
        self.assertEqual(f._expect(None, {"max": 5})[0], False)


class Batch2(unittest.TestCase):
    """0.85.0: the sessions and logon times reports, messages, host maintenance."""

    def sess(self, i, user, mach, state="CONNECTED", start_h=1, **kw):
        d = {"id": i, "user_id": user, "machine_id": mach, "desktop_pool_id": "p1", "session_state": state, "start_time": NOW - start_h * H,
             "session_protocol": "BLAST", "client_data": {"type": "WINDOWS", "version": "8.12.0"}}
        d.update(kw)
        return d

    def test_versions(self):
        self.assertLess(f._version("8.9.0"), f._version("8.10"))
        self.assertLess(f._version("2306"), f._version("2312"))
        self.assertEqual(f._version("5.5.0 build-123"), (5, 5, 0))
        self.assertEqual(f._version(""), ())

    def test_sessions_report(self):
        ms = [mach("1", "p1", "CONNECTED"), mach("2", "p1", "AGENT_UNREACHABLE"), mach("3", "p1", "CONNECTED")]
        ss = [self.sess("a", "S-1", "1", client_data={"type": "WINDOWS", "version": "8.9.0"}),
              self.sess("b", "S-2", "2", "DISCONNECTED", 2),
              self.sess("c", "S-1", "3", "PENDING", 0.5, session_protocol="PCOIP"),
              self.sess("d", "S-3", "", "CONNECTED", 80, session_type="APPLICATION", client_data={"type": "MAC", "version": "8.1"})]
        r = f.hz_sessions_report(data([IC], ms, ss), {"now": NOW, "client_min": {"WINDOWS": "8.10", "*": "8.0"}, "pending_minutes": 10,
                                                      "long_hours": 72, "protocols": ["blast"]})
        self.assertEqual((r["old_clients"], r["pending"], r["on_broken"], r["long"], r["duplicates"]), (1, 1, 1, 1, 1))
        self.assertEqual(r["unnamed"], ["S-1", "S-2", "S-3"])
        self.assertEqual(r["warning"], 4)
        r = f.hz_sessions_report(data([IC], ms, ss, ad_users=[{"id": "S-1", "login_name": "jdoe", "domain": "CORP"}]), {"now": NOW})
        self.assertNotIn("S-1", r["unnamed"])
        self.assertEqual(r["old_clients"], 0)                                     # no baseline
        self.assertEqual(r["pending"], 1)                                         # 10 minutes by default

    def test_logon(self):
        tree = {"n": "brokerLaunch", "d": 90, "c": [{"n": "machineSelection", "d": 5},
                                                    {"n": "clientLogon", "d": 80, "c": [{"n": "userProfile", "d": 60}, {"n": "userGpo", "d": 15}]}]}
        self.assertEqual(f._phases(tree)[0], ("brokerLaunch", 90.0, 0))
        self.assertEqual(f._phases({"logon": {"duration_ms": 45000}}), [("logon", 45.0, 0)])
        raw = {"logon:a": [{"logon_segment_data": __import__("json").dumps(tree)}], "logon:b": [{}],
               "logon:c": [{"logon_segment_data": [{"n": "x", "d": 20}, {"n": "y", "d": 30}]}]}
        ss = [self.sess("a", "S-1", "1"), self.sess("b", "S-2", "2"), self.sess("c", "S-3", "3")]
        r = f.hz_logon(data([IC], [mach("1", "p1", "CONNECTED")], ss, **raw), {"warn_s": 60, "crit_s": 120})
        self.assertEqual((r["timed"], r["missing"], r["warning"], r["critical"]), (2, 1, 1, 0))
        slow = r["report"]["sections"][1]["rows"][0]
        self.assertEqual((slow[4], slow[5]), ("90", "userProfile 60 s"))          # the innermost phase, not clientLogon
        self.assertEqual(r["report"]["sections"][1]["rows"][1][4], "50")          # no root: the phases summed
        r = f.hz_logon(data([IC], [], ss, **{"logon:b": [{}]}), {})
        self.assertIn("turn on the timing profiler", r["report"]["sections"][0]["text"])
        self.assertEqual(f.hz_recent_sessions(data([IC], [], ss + [self.sess("o", "S-9", "", start_h=30)]), 24, NOW, 2), ["a", "b"])

    def test_messages(self):
        ms = [mach("1", "p1", "CONNECTED", managed_machine_data={"host_name": "esx1.example.mil"}), mach("2", "p3", "CONNECTED")]
        ss = [self.sess("a", "S-1", "1"), self.sess("b", "S-2", "2", desktop_pool_id="p3")]
        d = data([IC, PROT], ms, ss, ad_users=[{"id": "S-2", "login_name": "asmith", "domain": "CORP"}])
        t = lambda **o: [x["id"] for x in f.hz_msg_targets(d, dict(o, message="hi"))["sessions"]]   # noqa: E731
        self.assertEqual(t(pools=["IC-*"]), ["a"])
        self.assertEqual(t(host="esx1"), ["a"])
        self.assertEqual(t(users=["CORP\\asmith"]), ["b"])
        self.assertEqual(t(users=["asmith"]), ["b"])
        self.assertEqual(t(all=True), ["a", "b"])
        self.assertIn("Nobody to message", f.hz_msg_targets(d, {"message": "hi"})["problems"][0])
        self.assertIn("No message", f.hz_msg_targets(d, {"all": True})["problems"][0])

    def test_host_maintenance(self):
        on = {"host_name": "esx2.example.mil"}
        ms = [mach("1", "p1", "AVAILABLE", managed_machine_data=on), mach("2", "p1", "CONNECTED", managed_machine_data=on),
              mach("3", "p3", "AVAILABLE", managed_machine_data=on), mach("4", "p1", "MAINTENANCE", managed_machine_data=on),
              mach("5", "p1", "AVAILABLE", managed_machine_data={"host_name": "esx3.example.mil"})]
        d = data([IC, PROT], ms, [self.sess("s", "S-1", "2")])
        p = f.hz_host_plan(d, "ESX2", {"protected_pools": ["VIP-*"]})
        self.assertEqual([x["id"] for x in p["desktops"]], ["1"])
        self.assertEqual(sorted(x["id"] for x in p["held"]), ["2", "3", "4"])
        self.assertEqual([x["id"] for x in p["sessions"]], ["s"])
        self.assertEqual(p["unnamed"], ["S-1"])
        self.assertIn("Horizon has no desktop on host esx9 (hosts it knows: esx2.example.mil, esx3.example.mil)",
                      f.hz_host_plan(d, "esx9")["problems"][0])
        # after the approval: moved, taken
        ms2 = [mach("1", "p1", "AVAILABLE", managed_machine_data={"host_name": "esx3.example.mil"})]
        rc = f.hz_host_recheck({"host": "esx2", "desktops": p["desktops"]}, data([IC], ms2), {})
        self.assertEqual(rc["skipped"][0]["reason"], "it is on host esx3.example.mil now")
        rc = f.hz_host_recheck({"host": "esx2", "desktops": p["desktops"]}, d, {"protected_pools": ["VIP-*"]})
        self.assertEqual(([x["id"] for x in rc["act"]], [x["id"] for x in rc["sessions"]]), (["1"], ["s"]))
        back = f.hz_host_in_maintenance(data([IC, PROT], ms + [mach("6", "p3", "MAINTENANCE", managed_machine_data=on)]), "esx2",
                                        {"protected_pools": ["VIP-*"]})
        self.assertEqual(([x["id"] for x in back["act"]], [x["id"] for x in back["held"]]), (["4"], ["6"]))


class Module(unittest.TestCase):
    def setUp(self):
        try:
            import site_horizon
        except ImportError:                    # no ansible-core here: the filters are tested, the module is not
            self.skipTest("ansible-core is not importable")
        self.m = site_horizon

    def test_connection(self):
        c = self.m.connection({"HORIZON_URL": "horizon.example.mil/rest/", "HORIZON_USERNAME": "CORP\\svc", "HORIZON_PASSWORD": "p",
                               "HORIZON_VERIFY_SSL": "False"})
        self.assertEqual((c["url"], c["domain"], c["user"], c["verify"]), ("https://horizon.example.mil", "CORP", "svc", False))
        c = self.m.connection({"HORIZON_URL": "https://h.example.mil", "HORIZON_USER": "svc@corp.example.mil", "HORIZON_PASSWORD": "p"})
        self.assertEqual((c["domain"], c["user"], c["verify"]), ("corp.example.mil", "svc", True))
        api = self.m.Api(self.m.connection({}))
        self.assertIn('attach a credential of type "Horizon API"', api.login())
        api = self.m.Api(self.m.connection({"HORIZON_URL": "h", "HORIZON_USERNAME": "svc", "HORIZON_PASSWORD": "p"}))
        self.assertIn("No domain for the Horizon login", api.login())

    def test_bulk(self):
        r = self.m.bulk_results(["a", "b", "c"], 200, [{"id": "a", "status_code": 200}, {"id": "b", "status_code": 400,
                                                        "errors": [{"error_message": "in use"}]}], "reset")
        self.assertEqual([(x["id"], x["ok"]) for x in r], [("a", True), ("b", False), ("c", True)])
        self.assertEqual(r[1]["error"], "reset refused (HTTP 400): in use")
        r = self.m.bulk_results(["a"], 403, {"error_message": "Not authorized"}, "reset")
        self.assertEqual(r[0]["error"], "HTTP 403: Not authorized")
        self.assertEqual(self.m.error_text({"errors": [{"error_key": "k"}]}), "k")

    def test_act_check_mode_and_verbs(self):
        api = self.m.Api(self.m.connection({}))
        self.assertEqual(self.m.act(api, "machines", "recover", ["1"], {}, {}, "v1", True)[0]["status"], "would")
        with self.assertRaises(ValueError):
            self.m.act(api, "machines", "format", ["1"], {}, {}, "v1", True)
        with self.assertRaises(ValueError):
            self.m.act(api, "pool", "delete", ["1"], {}, {}, "v1", True)
        self.assertEqual(self.m.act(api, "sessions", "logoff", [" "], {}, {}, "v1", False), [])


if __name__ == "__main__":
    unittest.main()
