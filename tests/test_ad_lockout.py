#!/usr/bin/env python3
"""Unit tests for the account lockout job (plugins/filter/ad_lockout_filters.py): the input, the DC that
reads each domain, the accounts to look for, and the report built from what the scripts answered for a
made-up domain (tests/ad/fixtures/lockout_*.json, made by tests/ad/make_lockout_fixtures.sh from
tests/windows/run_lockout_scenario.ps1). Standard library only: python3 tests/test_ad_lockout.py"""
import copy
import datetime
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import ad_lockout_filters as f  # noqa: E402
import site_filters as sf  # noqa: E402


def fx(name):
    with open(os.path.join(HERE, "ad", "fixtures", "lockout_" + name + ".json")) as h:
        return json.load(h)


def find(fixture="find_one", host="dc01.corp.test", **over):
    d = copy.deepcopy(fx(fixture)["find"])
    d.update(over)
    return {"host": host, "reachable": True, "error": "", "data": d}


def dc(name, host):
    return {"host": host, "reachable": True, "error": "", "data": copy.deepcopy(fx(name)["dc"])}


DOWN = {"host": "dc03.corp.test", "reachable": False, "error": "ntlm: rejected", "data": None}
NOW = fx("dc_pdc")["dc"]["read_at"]
INV = [{"name": "pc123.corp.test", "address": ""}, {"name": "dc01.corp.test", "address": "192.0.2.1"}]


def finds(name="find_one"):
    return [find(name), find(name, "dc02.corp.test", name="DC02", role="dc", users=[], locked=[]), DOWN]


def dcs():
    return [dc("dc_pdc", "dc01.corp.test"), dc("dc_bdc", "dc02.corp.test"), DOWN]


def report(fs=None, ds=None, unlocks=None, sources=None, **opts):
    o = {"hours": 24, "asked": True, "now": NOW, "exchange_servers": ["EXCH*"], "inventory": INV}
    o.update(opts)
    return f.ad_lockout_report(fs if fs is not None else finds(), ds if ds is not None else dcs(), unlocks, sources, o)


def section(r, title_part):
    for s in r["report"]["sections"]:
        if title_part in s["title"]:
            return s
    raise AssertionError("no section with " + title_part)


class Input(unittest.TestCase):
    def test_names(self):
        r = f.ad_lockout_names("CORP\\jdoe, jdoe@corp.test\n  o'brien ;CORP\\JDOE")
        self.assertEqual((r["names"], r["problems"]), (["CORP\\jdoe", "jdoe@corp.test", "o'brien"], []))   # one per line or comma; twice = once
        self.assertEqual(f.ad_lockout_names(None)["names"], [])
        self.assertEqual(f.ad_lockout_names(["a", "", " b "])["names"], ["a", "b"])

    def test_refused(self):
        for bad in ("jdoe*", "j[doe]", "a\\b\\c", "x@y@z", "CORP\\", "=x", 'say"'):
            self.assertIn("is not an account name", " ".join(f.ad_lockout_names(bad)["problems"]), bad)
        self.assertIn("at most 2 at a time", f.ad_lockout_names("a,b,c", 2)["problems"][0])


class Pick(unittest.TestCase):
    def test_pdc_reads(self):
        fs = [find(host="dc02.corp.test", role="dc"), find()]
        self.assertEqual(f.ad_lockout_pick(fs), {"corp.test": "dc01.corp.test"})     # the PDC, not the first DC
        self.assertEqual(f.ad_lockout_pick([find(host="dc02.corp.test", role="dc"), DOWN]), {"corp.test": "dc02.corp.test"})
        self.assertEqual(f.ad_lockout_pick([find("find_no_ad"), DOWN]), {})                 # no module: cannot read

    def test_targets(self):
        t = f.ad_lockout_targets([find("find_upn")], {"corp.test": "dc01.corp.test"})
        self.assertEqual(t["not_found"], ["nobody", "OTHER\\x"])
        self.assertEqual([a["sam"] for a in t["by_domain"]["corp.test"]], ["jdoe", "o'brien"])
        self.assertEqual((t["accounts"][0]["netbios"], t["accounts"][0]["reader"]), ("CORP", "dc01.corp.test"))
        t = f.ad_lockout_targets([find("find_all")], {"corp.test": "dc01.corp.test"})
        self.assertEqual(([a["sam"] for a in t["accounts"]], t["not_found"]), (["jdoe", "erin"], []))
        # only the reading DC's answer counts
        t = f.ad_lockout_targets([find("find_upn", "dc02.corp.test", role="dc"), find()], {"corp.test": "dc01.corp.test"})
        self.assertEqual(t["not_found"], [])


class Sources(unittest.TestCase):
    def test_ranked(self):
        r = report()
        a = r["accounts"][0]
        self.assertEqual((a["account"], a["locked"], a["caller"]), ("CORP\\jdoe", True, "PC123"))
        top = a["sources"][0]
        # 4771 by address (resolved to pc123), 4776 by workstation \\PC123 and the 4740 caller: one computer
        self.assertEqual((top["source"], top["addresses"], top["bad"], top["after"], top["lockouts"]), ("PC123", ["192.0.2.50"], 5, 3, 1))
        self.assertEqual([s["source"] for s in a["sources"]], ["PC123", "EXCH01", "WS9", "192.0.2.99"])   # an address with no name stays
        self.assertEqual(r["report"]["title"], "Account lockout: CORP\\jdoe LOCKED OUT - from PC123")
        self.assertEqual(r["report"]["status"], "warning")
        self.assertEqual((r["dcs_read"], r["dcs_total"]), (2, 3))

    def test_hints(self):
        lines = " | ".join(section(report(), "what to do")["lines"])
        for want in ("CORP\\jdoe is LOCKED OUT since", "(recorded by DC01)", "locks an account out after 5 bad passwords within 30 minutes, for 30 minutes",
                     "Most bad passwords came from PC123 (192.0.2.50): 8 failed sign-ins, 1 lockout",
                     "PC123 kept trying after the account was locked out (3 times)", "EXCH01 is a mail server",
                     "WS9: something on it opens a share", "To check a computer by hand"):
            self.assertIn(want, lines)
        self.assertNotIn("not in this job's inventory", lines)         # only said when the sources are looked on

    def test_steady_pace(self):
        ds = dcs()
        base = f.parse_time(NOW)
        ds[0]["data"]["events"] = [{"user": "jdoe", "id": 4771, "status": "0x18", "ip": "192.0.2.50",
                                    "time": (base - datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")} for i in range(6)]
        ds[1]["data"]["events"] = []
        lines = " | ".join(section(report(ds=ds), "what to do")["lines"])
        self.assertIn("PC123 tries about every 5 minutes: a saved password retrying", lines)
        self.assertEqual(f._interval([base, base + datetime.timedelta(minutes=1), base + datetime.timedelta(minutes=40),
                                      base + datetime.timedelta(minutes=41)]), "")              # bursts: no pace

    def test_status_codes(self):
        self.assertEqual(f._status({"id": 4771, "status": "0x18"}), ("bad password", "bad"))
        self.assertEqual(f._status({"id": 4768, "status": "0x12"}), ("account locked out or disabled", "after"))
        self.assertEqual(f._status({"id": 4625, "status": "0xc000006d", "sub_status": "0xc000006a"}), ("bad password", "bad"))
        self.assertEqual(f._status({"id": 4776, "status": "0xc0000234"})[1], "after")
        self.assertEqual(f._status({"id": 4776, "status": "0xc0000071"}), ("password expired", "other"))
        self.assertEqual(f.short("\\\\pc1.corp.test"), "PC1")
        self.assertEqual(f.short("PC1$"), "PC1")
        self.assertEqual(f.short("192.0.2.5"), "192.0.2.5")

    def test_blank_caller_and_dc(self):
        ds = dcs()
        ds[0]["data"]["events"] = [{"user": "jdoe", "id": 4740, "computer": "", "time": NOW},
                                   {"user": "jdoe", "id": 4776, "status": "0xc000006a", "computer": "DC02", "time": NOW}]
        ds[1]["data"]["events"] = []
        r = report(ds=ds)
        lines = " | ".join(section(r, "what to do")["lines"])
        self.assertIn("The DC did not record which computer", lines)
        self.assertIn("DC02 is a domain controller: the sign-ins came through it", lines)
        self.assertEqual(r["accounts"][0]["caller"], "(not recorded)")

    def test_no_events(self):
        ds = dcs()
        for d in ds[:2]:
            d["data"]["events"] = []
        lines = " | ".join(section(report(ds=ds), "what to do")["lines"])
        self.assertIn("No failed sign-in for jdoe in the Security logs of the last 24 h: the DCs may not audit Account Lockout", lines)

    def test_per_dc(self):
        s = section(report(), "on each domain controller")
        self.assertEqual(s["rows"][0][:4], ["DC01 (PDC)", 5, s["rows"][0][2], "yes"])
        self.assertEqual(s["rows"][1][:2], ["DC02", 3])
        self.assertEqual((s["rows"][2][1], s["row_status"][2]), ("UNREACHABLE", "critical"))
        self.assertIn("DC03: UNREACHABLE - ntlm: rejected", section(report(), "Not read")["lines"])

    def test_lockouts_and_unlocks(self):
        rows = section(report(), "lockouts and unlocks")["rows"]
        self.assertEqual([r[2:] for r in rows], [["locked out", "PC123"], ["unlocked", "CORP\\helpdesk1"]])

    def test_capped(self):
        ds = dcs()
        ds[1]["data"]["capped"] = ["jdoe"]
        self.assertIn("DC02 had more events for jdoe than ad_lockout_max_events", " ".join(section(report(ds=ds), "what to do")["lines"]))


class Unlock(unittest.TestCase):
    def test_unlocked(self):
        u = [{"host": "dc01.corp.test", "domain": "corp.test", "error": "", "data": fx("unlock_ok")["unlock"]}]
        r = report(unlocks=u, unlock=True)
        self.assertEqual(r["report"]["title"], "Account lockout: CORP\\jdoe unlocked by this job - from PC123")
        self.assertEqual((r["report"]["status"], r["accounts"][0]["unlocked"], r["accounts"][0]["unlock_error"]), ("info", True, ""))
        lines = section(r, "what to do")["lines"]
        self.assertTrue(lines[0].startswith("UNLOCKED by this job on DC01. It locks out again"))
        self.assertIn("CORP\\jdoe was locked out since", lines[1])

    def test_refused(self):
        u = [{"host": "dc01.corp.test", "domain": "corp.test", "error": "", "data": fx("unlock_denied")["unlock"]}]
        a = report(unlocks=u, unlock=True)["accounts"][0]
        self.assertEqual((a["unlocked"], a["unlock_error"]), (False, "Insufficient access rights to perform the operation"))
        # the script itself broke: every locked account of the domain is not unlocked
        u = [{"host": "dc01.corp.test", "domain": "corp.test", "error": "the unlock gave no result", "data": {"unlocked": []}}]
        self.assertEqual(report(unlocks=u, unlock=True)["accounts"][0]["unlock_error"], "the unlock gave no result")

    def test_dry_run_and_not_locked(self):
        self.assertIn("Would be unlocked (dry run)", section(report(unlock=True, check_mode=True), "what to do")["lines"][0])
        fs = finds()
        fs[0]["data"]["users"][0]["locked"] = False
        r = report(fs=fs, unlock=True)
        self.assertEqual(section(r, "what to do")["lines"][0], "Not locked out: nothing to unlock.")
        self.assertTrue(r["report"]["title"].startswith("Account lockout: CORP\\jdoe not locked out - 12 failed sign-ins in 24 h"))
        self.assertEqual(r["report"]["status"], "ok")


class SourceComputers(unittest.TestCase):
    def test_which_to_look_on(self):
        r = report(check_sources=True)
        self.assertEqual(list(r["check"]), ["pc123.corp.test"])          # in the inventory; EXCH01, WS9 are not
        self.assertEqual(r["check"]["pc123.corp.test"][0], {"sam": "jdoe", "domain": "corp.test", "netbios": "CORP",
                                                            "upn": "jdoe@corp.test", "sid": "S-1-5-21-1-2-3-1105"})
        self.assertIn("WS9 is not in this job's inventory: check it by hand", " ".join(section(r, "what to do")["lines"]))
        # by the address the inventory gives it
        inv = [{"name": "workstation9", "address": "192.0.2.60"}]
        self.assertEqual(list(report(check_sources=True, inventory=inv)["check"]), ["workstation9"])
        self.assertEqual(report(check_sources=True, check_max=1, inventory=inv)["check"], {})   # only the worst one looked on

    def test_findings(self):
        src = [{"host": "pc123.corp.test", "reachable": True, "error": "", "data": fx("source_pc")["source"]},
               {"host": "pc9.corp.test", "reachable": False, "error": "timed out", "data": None}]
        r = report(sources=src, check_sources=True)
        lines = " | ".join(section(r, "what to do")["lines"])
        for want in ("On PC123: the service BackupAgent (Backup Agent - Running, Auto) logs on as CORP\\jdoe",
                     "the scheduled task \\Copy reports runs as CORP\\jdoe", "has a DISCONNECTED session",
                     "logoff 3", "drive H: mapped to \\\\files\\home\\jdoe", "OUTLOOK runs as CORP\\jdoe (2 running)"):
            self.assertIn(want, lines)
        self.assertEqual(len(section(r, "on the source computers")["rows"]), 6)
        self.assertIn("PC9 (source computer): UNREACHABLE - timed out", section(r, "Not read")["lines"])


class Everyone(unittest.TestCase):
    def test_locked_now(self):
        r = report(fs=finds("find_all"), asked=False)
        self.assertEqual(r["report"]["title"], "Account lockout: 2 account(s) locked out now (corp.test)")
        s = section(r, "Locked out now")
        self.assertEqual([row[0] for row in s["rows"]], ["CORP\\jdoe", "CORP\\erin"])
        self.assertEqual(s["rows"][0][3], "PC123 (8), EXCH01 (2), WS9 (1)")
        erin = " ".join(section(r, "CORP\\erin")["lines"])
        self.assertIn("fine-grained password policy Admins-PSO locks an account out after 3 bad passwords within 15 minutes, until someone unlocks it", erin)

    def test_none(self):
        fs = finds("find_all")
        fs[0]["data"]["locked"] = []
        fs[0]["data"]["locked_total"] = 0
        r = report(fs=fs, asked=False)
        self.assertEqual((r["report"]["title"], r["report"]["status"]), ("Account lockout: no account locked out (corp.test)", "ok"))
        self.assertEqual(section(r, "Locked out now")["text"], "No account is locked out.")

    def test_more_than_shown(self):
        fs = finds("find_all")
        fs[0]["data"]["locked_total"] = 40
        self.assertIn("40 are locked out; the latest 2 are shown", section(report(fs=fs, asked=False), "Locked out now")["text"])

    def test_text(self):
        t = sf.report_text(report(fs=finds("find_all"), asked=False)["report"])
        self.assertIn("Account lockout: 2 account(s) locked out now", t)
        self.assertIn("Lockout caller (4740)", t)


if __name__ == "__main__":
    unittest.main()
