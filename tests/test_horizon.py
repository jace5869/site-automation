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
                  mach("7", "p1", "PROVISIONING_ERROR", user_ids=["S-1-5-21-1"]), mach("8", "p1", "PROVISIONING")],
                 [{"id": "s1", "machine_id": "1", "session_state": "PENDING"}])
        r = f.hz_desktops(d, {"protected_pools": ["vip-*"], "actions": {"agent_err_need_reboot": "restart", "PROVISIONING_ERROR": "remove"}})
        fix = dict((x["id"], x["action"]) for x in r["fix"])
        held = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertEqual(fix, {"2": "restart"})
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


class Sessions(unittest.TestCase):
    def test_old_ones(self):
        ss = [{"id": "a", "user_id": "S-1-5-21-1", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 9 * H},
              {"id": "b", "user_id": "S-2", "machine_id": "1", "desktop_pool_id": "p1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 2 * H},
              {"id": "c", "user_id": "S-3", "machine_id": "", "farm_id": "f1", "session_state": "DISCONNECTED", "disconnected_time": NOW - 30 * H},
              {"id": "d", "user_id": "S-4", "desktop_pool_id": "p3", "session_state": "DISCONNECTED", "disconnected_time": NOW - 30 * H},
              {"id": "e", "user_id": "S-5", "desktop_pool_id": "p1", "session_state": "DISCONNECTED"},
              {"id": "g", "user_id": "S-6", "desktop_pool_id": "p1", "session_state": "CONNECTED"}]
        d = data([IC, PROT], [mach("1", "p1", "DISCONNECTED")], ss, farms=[{"id": "f1", "name": "Farm A"}])
        r = f.hz_sessions_old(d, {"now": NOW, "hours": 8, "protected_pools": ["VIP-*"], "names": {"S-1-5-21-1": "CORP\\jdoe"},
                                  "exclude_users": []})
        self.assertEqual([x["id"] for x in r["logoff"]], ["c", "a"])          # the oldest first
        self.assertEqual(r["logoff"][1]["label"], "CORP\\jdoe on d-1")
        self.assertEqual((r["logoff"][0]["machine"], r["logoff"][0]["pool"]), ("(an RDS host)", "farm Farm A"))
        held = dict((x["id"], x["reason"]) for x in r["held"])
        self.assertIn("pool VIP-Desk is excluded", held["d"])
        self.assertEqual(held["e"], "when it was disconnected is not known")
        self.assertEqual(r["recent"], 1)
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
        u = f.hz_user(self.d, "nora")                         # no AD record: the name a session carries
        self.assertEqual(([s["id"] for s in u["sessions"]], u["problems"]), (["s4"], []))
        self.assertIn("Horizon knows no user ghost", f.hz_user(self.d, "ghost")["problems"][0])
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
        self.assertIn("Horizon flags golden image VM gold: X", f.hz_pick(vms, "/dc2/gold")["warnings"][0])
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
