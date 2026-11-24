#!/usr/bin/env python3
"""Windows - schedule a restart / cancel a scheduled restart (playbooks/win_restart_schedule.yml, win_restart_cancel.yml,
roles/win_restart, plugins/filter/win_restart_filters.py): the 24-hour time (a passed time = tomorrow, too close =
refused, a time the clocks skip = refused, one they pass twice = the first, bad input); the site zone to UTC and, under
pwsh with TZ set, UTC to the server's own time; the pending-reboot decision from fake registry results (pwsh); the task's
script at the restart time with a stand-in shutdown.exe (restart, or skipped when nothing is pending); the comment; the
model and the reports (schedule and cancel) rendered in the atlas look; the catalog entries; the playbooks' syntax.
  python3 tests/test_win_restart.py"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import win_restart_filters as w  # noqa: E402

U = datetime.timezone.utc
# pwsh keeps a startup cache in $XDG_CACHE_HOME/powershell that two pwsh starting at once can corrupt (then every pwsh
# fails to start): one of this test's own, and the playbook runs with one fork (their two stand-in servers one at a time)
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(os.environ.get("TMPDIR") or tempfile.gettempdir(), "win-restart-pwsh-cache"))
FILES = os.path.join(REPO, "roles", "win_restart", "files")
PS = os.path.join(FILES, "win_restart.ps1")
TASK = os.path.join(FILES, "win_restart_task.ps1")


def at(*a):
    return datetime.datetime(*a, tzinfo=U)


class When(unittest.TestCase):
    def test_today_and_formats(self):
        r = w.win_restart_when("22:30", "Europe/Berlin", 5, at(2026, 11, 24, 9, 0))
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["utc"], r["site"], r["day"]), ("2026-11-24T21:30:00Z", "Tue 2026-11-24 22:30 CET", "today"))
        for typed in ("2230", " 22:30 "):
            self.assertEqual(w.win_restart_when(typed, "Europe/Berlin", 5, at(2026, 11, 24, 9, 0))["utc"], "2026-11-24T21:30:00Z")
        self.assertEqual(w.win_restart_when("7:05", "UTC", 5, at(2026, 1, 1))["utc"], "2026-01-01T07:05:00Z")
        self.assertEqual(w.win_restart_when("00:00", "UTC", 5, at(2026, 1, 1, 12))["utc"], "2026-01-02T00:00:00Z")

    def test_passed_means_tomorrow(self):
        r = w.win_restart_when("08:00", "America/New_York", 5, at(2026, 11, 24, 15, 0))     # 10:00 EST
        self.assertEqual((r["utc"], r["day"]), ("2026-11-25T13:00:00Z", "tomorrow"))
        self.assertIn("has passed", r["note"])
        self.assertTrue(w.win_restart_when("10:00", "America/New_York", 5, at(2026, 11, 24, 15, 0))["day"] == "tomorrow")  # now = passed

    def test_too_close_refused(self):
        r = w.win_restart_when("10:03", "America/New_York", 5, at(2026, 11, 24, 15, 0))
        self.assertFalse(r["ok"])
        self.assertIn("less than 5 minutes", r["error"])
        self.assertTrue(w.win_restart_when("10:06", "America/New_York", 5, at(2026, 11, 24, 15, 0))["ok"])

    def test_dst_gap_refused(self):
        # US: 2026-03-08 02:00 -> 03:00; EU: 2026-03-29 02:00 -> 03:00 (checked through zoneinfo, so a wrong date fails here)
        import zoneinfo
        ny, be = zoneinfo.ZoneInfo("America/New_York"), zoneinfo.ZoneInfo("Europe/Berlin")
        self.assertNotEqual(datetime.datetime(2026, 3, 7, 12, tzinfo=ny).utcoffset(), datetime.datetime(2026, 3, 8, 12, tzinfo=ny).utcoffset())
        self.assertNotEqual(datetime.datetime(2026, 3, 28, 12, tzinfo=be).utcoffset(), datetime.datetime(2026, 3, 29, 12, tzinfo=be).utcoffset())
        r = w.win_restart_when("02:30", "America/New_York", 5, at(2026, 3, 7, 20, 0))       # Sat 15:00 EST: tomorrow 02:30 does not exist
        self.assertFalse(r["ok"])
        self.assertIn("does not exist on 2026-03-08", r["error"])
        r = w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 3, 28, 12, 0))
        self.assertIn("does not exist on 2026-03-29", r["error"])
        # the hour after the gap is fine, in summer time
        r = w.win_restart_when("03:30", "Europe/Berlin", 5, at(2026, 3, 28, 12, 0))
        self.assertEqual((r["utc"], r["site"]), ("2026-03-29T01:30:00Z", "Sun 2026-03-29 03:30 CEST"))

    def test_dst_twice_first(self):
        r = w.win_restart_when("01:30", "America/New_York", 5, at(2026, 10, 31, 20, 0))     # US: 2026-11-01 back
        self.assertEqual((r["utc"], r["site"]), ("2026-11-01T05:30:00Z", "Sun 2026-11-01 01:30 EDT"))
        self.assertIn("happens twice", r["note"])
        r = w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 10, 24, 12, 0))         # EU: 2026-10-25 back
        self.assertEqual(r["utc"], "2026-10-25T00:30:00Z")
        self.assertIn("the first one, CEST", r["note"])

    def test_tomorrow_across_the_change(self):
        # Saturday 12:00 CEST, 11:00 typed: tomorrow (Sunday) is winter time already - CET, UTC+1
        r = w.win_restart_when("11:00", "Europe/Berlin", 5, at(2026, 10, 24, 10, 0))
        self.assertEqual((r["utc"], r["site"]), ("2026-10-25T10:00:00Z", "Sun 2026-10-25 11:00 CET"))
        self.assertEqual(r["note"], "11:00 today has passed (it is 12:00 CEST now): tomorrow.")

    def test_lead_in_real_time_across_the_change(self):
        # Berlin 2026-03-29, 01:59 CET (00:59 UTC): 03:02 CEST is 01:02 UTC - 3 minutes away in real time: refused
        r = w.win_restart_when("03:02", "Europe/Berlin", 5, at(2026, 3, 29, 0, 59))
        self.assertFalse(r["ok"])
        self.assertIn("less than 5 minutes", r["error"])
        r = w.win_restart_when("03:10", "Europe/Berlin", 5, at(2026, 3, 29, 0, 59))
        self.assertEqual(r["utc"], "2026-03-29T01:10:00Z")
        # the skipped hour: before the jump it is refused; after it, tomorrow
        self.assertIn("does not exist", w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 3, 29, 0, 30))["error"])
        r = w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 3, 29, 1, 10))          # 03:10 CEST
        self.assertEqual((r["utc"], r["day"]), ("2026-03-30T00:30:00Z", "tomorrow"))

    def test_twice_first_has_passed(self):
        # Berlin 2026-10-25 02:10 CET (01:10 UTC, the second 02:10): the first 02:30 (00:30 UTC) has passed - the second, 01:30 UTC
        r = w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 10, 25, 1, 10))
        self.assertEqual((r["utc"], r["site"], r["day"]), ("2026-10-25T01:30:00Z", "Sun 2026-10-25 02:30 CET", "today"))
        self.assertIn("the second one (the first has passed)", r["note"])
        # 02:13 then: the second is 3 minutes away in real time: refused, never the first (past)
        self.assertIn("less than 5 minutes", w.win_restart_when("02:13", "Europe/Berlin", 5, at(2026, 10, 25, 1, 10))["error"])
        # 02:05 then: both have passed - tomorrow
        self.assertEqual(w.win_restart_when("02:05", "Europe/Berlin", 5, at(2026, 10, 25, 1, 10))["utc"], "2026-10-26T01:05:00Z")
        # still in the first 02:xx (00:10 UTC = 02:10 CEST): 02:30 is the first one
        r = w.win_restart_when("02:30", "Europe/Berlin", 5, at(2026, 10, 25, 0, 10))
        self.assertEqual(r["utc"], "2026-10-25T00:30:00Z")
        self.assertIn("the first one, CEST", r["note"])

    def test_bad_input(self):
        for typed in ("", "24:00", "7:5", "12:60", "noon", "1230pm", "12.30", "-1:00", None):
            r = w.win_restart_when(typed, "UTC", 5, at(2026, 1, 1))
            self.assertFalse(r["ok"], typed)
            self.assertIn("24-hour", r["error"])
        self.assertIn("no time zone", w.win_restart_when("12:00", "", 5, at(2026, 1, 1))["error"])
        self.assertIn("not one this execution environment knows", w.win_restart_when("12:00", "Mars/Base", 5, at(2026, 1, 1))["error"])


class Checks(unittest.TestCase):
    def ok(self, **k):
        o = dict(reason="Monthly patches", change="CHG0012345", code="p:0:0", delay=60, limit="srv-app01,srv-app02", target="", hosts=3, max_hosts=50)
        o.update(k)
        return w.win_restart_check(o)

    def test_answers(self):
        self.assertEqual(self.ok(), [])
        self.assertIn("Reason is empty", self.ok(reason=" ")[0])
        self.assertIn("change number", self.ok(change="CHG 1; rm")[0])
        self.assertIn("reason_code", self.ok(code="0:0")[0])
        self.assertIn("delay", self.ok(delay=9999)[0])
        for lim, tgt in (("", ""), ("  ", None), ("all", ""), ("*", ""), ("!srv-app01", ""), ("", "all"), (",", " ")):
            self.assertIn("no servers picked", self.ok(limit=lim, target=tgt)[0], (lim, tgt))
        self.assertEqual(self.ok(limit="", target="srv-app01"), [])
        self.assertEqual(self.ok(limit="all", target="srv-app01"), [])
        self.assertIn("left out by site_exclude_hosts", self.ok(excluded_in=["srv-app01"])[0])
        # a Limit from a file: refused (an empty one would pick every server)
        for lim in ("@hosts.txt", "srv-app01,@more.txt"):
            self.assertIn("pick the servers by name", self.ok(limit=lim)[0])
        self.assertIn("pick the servers by name", self.ok(limit="", target="@hosts.txt")[0])
        # every server of the run was named; none resolved: refused
        self.assertIn("matches no server", self.ok(resolved=[])[0])
        self.assertIn("not named in the Limit: srv-db01", self.ok(resolved=["srv-app01", "srv-db01"])[0])
        self.assertEqual(self.ok(limit="web[01:03],grp", resolved=["web02.corp.example.mil", "srv-x"], groups={"grp": ["srv-x"]}), [])
        self.assertIn("web04", self.ok(limit="web[01:03]", resolved=["web04"])[0])
        self.assertEqual(self.ok(limit="SRV-APP*", resolved=["srv-app01.corp.example.mil"]), [])

    def test_hosts_pattern(self):
        # the exclusion is its own comma-separated part: 'A,B:!x' would bind it to B alone
        self.assertEqual(w.win_restart_hosts("A,B", ":!site_excluded"), "A,B,!site_excluded")
        self.assertEqual(w.win_restart_hosts(None, ":!site_excluded"), "all,!site_excluded")
        self.assertEqual(w.win_restart_hosts("web:&prod", ":!site_excluded"), "web,&prod,!site_excluded")
        self.assertEqual(w.win_restart_hosts("windows[0:4]:SRV-APP01", ""), "windows[0:4],SRV-APP01")
        self.assertEqual(w.win_restart_selector("web[01:03]", ""), ["web[01:03]"])
        self.assertIn("at most 50", self.ok(hosts=51)[0])

    def test_group_limits_and_regex(self):
        hosts = ["SRV-APP%02d" % n for n in range(1, 7)]
        groups = {"windows": hosts, "win_app": hosts[:2]}
        for term, expected in (("windows[0:4]", hosts[:5]), ("windows[0]", hosts[:1]), ("windows[-1]", hosts[-1:]),
                               ("win_a*", hosts[:2]), ("~^win_app$", hosts[:2]), (r"~APP0[1-3]$", hosts[:3])):
            with self.subTest(term=term):
                self.assertEqual(w.win_restart_unnamed(hosts, [term], groups), [h for h in hosts if h not in expected])

    def test_invalid_limit_refused(self):
        for term, why in (("web[z:a]", "reversed"), ("web[3:1]", "reversed"), ("web[0:999999999]", "exceeds"),
                          ("web[0:" + "9" * 4500 + "]", "oversized"), ("~[", "invalid Limit regex")):
            with self.subTest(why=why):
                errors = self.ok(limit="SRV-APP01," + term, resolved=["SRV-APP01"])
                self.assertIn(why, errors[0])

    def test_comment(self):
        c = w.win_restart_comment('Patch "now"\nplease', "chg001", 42)
        self.assertEqual(c, "CHG001: Patch 'now' please (site-automation, AAP job 42)")
        self.assertNotIn('"', c)
        self.assertLessEqual(len(w.win_restart_comment("x" * 2000, "CHG1", 1)), 500)
        self.assertEqual(w.win_restart_comment("Patch"), "Patch (site-automation)")


def res(host, reasons=(), restart=True, **k):
    r = {"computer": host.split(".")[0].upper(), "tz_id": "Eastern Standard Time", "boot": "2026-11-02 04:11", "uptime_days": 22.3,
         "at_local": "2026-11-24 22:30 UTC-05:00 (Eastern Standard Time)", "reasons": list(reasons), "restart": restart,
         "probes": {"cbs": "component servicing" in reasons, "wu": False, "file_renames": False, "rename": False, "ccm": None},
         "ours": []}
    r.update(k)
    return r


WHEN = {"ok": True, "time": "22:30", "zone": "America/New_York", "utc": "2026-11-25T03:30:00Z", "site": "Tue 2026-11-24 22:30 EST",
        "day": "today", "note": ""}
OLD = {"path": "\\site-automation\\", "name": "site-automation restart", "next_run": "2026-11-23 23:00", "description": "CHG1: old"}


def fleet(check):
    sched = {} if check else {"scheduled": {"path": "\\site-automation\\", "name": "site-automation restart", "next_run": "2026-11-24 22:30"}}
    return [
        {"host": "srv-app01.corp.example.mil", "result": res("srv-app01", ["component servicing", "Windows Update"], **sched)},
        {"host": "srv-app02.corp.example.mil", "result": res("srv-app02", [], restart=False,
                                                             probes={"file_renames": True}, ours=[OLD])},
        {"host": "srv-db01.corp.example.mil", "result": res("srv-db01", ["computer rename"],
                                                            probes={"rename": True, "active_name": "SRV-DB01", "pending_name": "SRV-DB02"},
                                                            **dict(sched, replaced=[OLD] if not check else [], ours=[OLD] if check else []))},
        {"host": "srv-web01.corp.example.mil", "result": {}, "unreached": "WinRM: the connection timed out"},
        {"host": "srv-web02.corp.example.mil", "result": {"error": "Access is denied"}},
    ]


class Model(unittest.TestCase):
    def test_plan(self):
        m = w.win_restart_model(fleet(True), {"mode": "schedule", "check": True, "only_if_pending": True, "comment": "CHG1: Patch"})
        st = dict((x["host"].split(".")[0], x["status"]) for x in m["rows"])
        self.assertEqual(st, {"srv-app01": "would restart", "srv-app02": "skipped", "srv-db01": "would restart",
                              "srv-web01": "not reached", "srv-web02": "failed"})
        self.assertEqual(m["rows"][0]["host"], "srv-web01.corp.example.mil")              # worst first
        app02 = next(x for x in m["rows"] if x["host"].startswith("srv-app02"))
        self.assertEqual(app02["pending"], "no")
        self.assertIn("not counted", app02["why"])
        db = next(x for x in m["rows"] if x["host"].startswith("srv-db01"))
        self.assertIn("SRV-DB01 -> SRV-DB02", db["why"])
        d = w.win_restart_doc(m, WHEN)
        self.assertEqual(d["title"], "Windows restart plan: 2 would restart at Tue 2026-11-24 22:30 EST, 1 skipped, 2 failed or not reached (nothing changed)")
        self.assertEqual([t["label"] for t in d["summary"]], ["Servers", "Would restart", "Skipped", "Reboot pending", "Failed or not reached"])
        self.assertEqual(d["summary"][3]["value"], 2)
        rows = d["sections"][0]["rows"]
        self.assertEqual(d["sections"][0]["columns"], ["Server", "Reboot pending", "Why", "Up since", "Restart at (server time)", "Result"])
        dbrow = next(r for r in rows if r[0] == "srv-db01")
        self.assertIn("replaces the restart at 2026-11-23 23:00", dbrow[5])
        self.assertIn("the earlier restart at 2026-11-23 23:00 would be removed", next(r for r in rows if r[0] == "srv-app02")[5])
        self.assertIn("Run it for real", [s["title"] for s in d["sections"]])
        self.assertIn("DRY RUN", d["footer"])
        items = w.win_restart_summary_items(m, WHEN)
        self.assertIn({"host": "srv-app01", "what": "restart at Tue 2026-11-24 22:30 EST", "level": "warning"}, items)

    def test_run(self):
        m = w.win_restart_model(fleet(False)[:3], {"mode": "schedule", "check": False, "only_if_pending": True})
        self.assertEqual([x["status"] for x in m["rows"]], ["scheduled", "scheduled", "skipped"])
        d = w.win_restart_doc(m, WHEN)
        self.assertEqual(d["status"], "ok")
        self.assertTrue(d["title"].startswith("Windows restart: 2 scheduled for Tue 2026-11-24 22:30 EST"))
        log = w.win_restart_log(m, WHEN)
        self.assertIn("replaced \\site-automation\\site-automation restart at 2026-11-23 23:00", " ".join(log))
        # a restart the script said yes to but no task came back from: failed, never "scheduled"
        m = w.win_restart_model([{"host": "a", "result": res("a", [])}], {"mode": "schedule", "check": False})
        self.assertEqual(m["rows"][0]["status"], "failed")
        # registration failed: the script's roll back words, never "replaces"
        m = w.win_restart_model([{"host": "a", "result": res("a", [], error="the task could not be registered right (x) - rolled back: "
                                                                    "the earlier restart (2026-11-23 23:00) is scheduled again")}], {"mode": "schedule"})
        self.assertIn("rolled back", m["rows"][0]["error"])
        self.assertNotIn("replaces", w.win_restart_doc(m, WHEN)["sections"][0]["rows"][0][5])
        # only if pending, none pending, an earlier unconditional restart of ours: removed, and said
        m = w.win_restart_model([{"host": "b", "result": res("b", [], restart=False, ours=[], removed=[OLD])}], {"mode": "schedule"})
        self.assertEqual(m["rows"][0]["status"], "skipped")
        self.assertIn("Skipped: no reboot pending; the earlier restart at 2026-11-23 23:00 was removed",
                      w.win_restart_doc(m, WHEN)["sections"][0]["rows"][0][5])

    def test_cancel(self):
        hosts = [{"host": "srv-app01.corp.example.mil", "result": {"ours": [OLD], "removed": [OLD]}},
                 {"host": "srv-app02.corp.example.mil", "result": {"ours": []}}]
        m = w.win_restart_model(hosts, {"mode": "cancel", "check": False})
        self.assertEqual([x["status"] for x in m["rows"]], ["removed", "nothing to cancel"])
        d = w.win_restart_cancel_doc(m)
        self.assertEqual(d["title"], "Windows restart cancel: 1 cancelled, 1 had none")
        self.assertEqual(d["sections"][0]["rows"][0][1], "\\site-automation\\site-automation restart")
        m = w.win_restart_model([dict(hosts[0], result={"ours": [OLD]})], {"mode": "cancel", "check": True})
        self.assertEqual(m["rows"][0]["status"], "would remove")
        self.assertIn("(nothing changed)", w.win_restart_cancel_doc(m)["title"])

    def test_parse(self):
        self.assertIn("error", w.win_restart_parse([]))
        self.assertIn("error", w.win_restart_parse([w.MARK + "{x"]))
        self.assertEqual(w.win_restart_parse(["x", w.MARK + '{"a": 1}'])["a"], 1)

    def test_rendered(self):
        from test_report_hover import render
        m = w.win_restart_model(fleet(True), {"mode": "schedule", "check": True, "only_if_pending": True, "comment": "CHG1: Patch"})
        page = render("report_atlas.html.j2", w.win_restart_doc(m, WHEN))
        self.assertIn("Would restart", page)
        self.assertIn("srv-db01", page)


def read(p):
    with open(p) as fh:
        return fh.read()


def pwsh(code, env=None, timeout=120):
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as fh:
        fh.write(code)
    try:
        return subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", fh.name], capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ, **(env or {})))
    finally:
        os.unlink(fh.name)


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
class PowerShell(unittest.TestCase):
    def test_parse(self):
        for p in (PS, TASK):
            r = subprocess.run(["pwsh", "-NoProfile", "-Command",
                                "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e);$e.Count" % p],
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.stdout.strip(), "0", r.stdout + r.stderr)
            with open(p) as fh:
                text = fh.read()
            self.assertNotIn("??", text)                      # Windows PowerShell 5.1: no PowerShell 7 operators
            self.assertNotIn("?.", text)
            self.assertTrue(all(ord(ch) < 128 for ch in text))   # written ASCII on the server

    def test_decision_from_fake_registry(self):
        code = "$SiteRestartLibrary = $true\n. '%s'\n" % TASK + r"""
$cases = @(
  @{ n = 'none'; p = @{ cbs = $false; wu = $false; file_renames = $false; rename = $false; ccm = $null } },
  @{ n = 'cbs+wu'; p = @{ cbs = $true; wu = $true; file_renames = $false; rename = $false; ccm = $null } },
  @{ n = 'ccm'; p = @{ cbs = $false; wu = $false; file_renames = $false; rename = $false; ccm = $true } },
  @{ n = 'ccm-no'; p = @{ cbs = $false; wu = $false; file_renames = $false; rename = $false; ccm = $false } },
  @{ n = 'rename'; p = @{ cbs = $false; wu = $false; file_renames = $false; rename = $true; ccm = $null } },
  @{ n = 'pfro'; p = @{ cbs = $false; wu = $false; file_renames = $true; rename = $false; ccm = $null } }
)
$out = @{}
foreach ($c in $cases) {
  $a = @(Get-SiteRestartReasons -Probes $c.p -CountFileRenames $false)
  $b = @(Get-SiteRestartReasons -Probes $c.p -CountFileRenames $true)
  $out[$c.n] = @{ reasons = $a; with_pfro = $b;
                  only = (Get-SiteRestartDecision -OnlyIfPending $true -Reasons $a).restart;
                  always = (Get-SiteRestartDecision -OnlyIfPending $false -Reasons $a).restart }
}
$out['null'] = (Get-SiteRestartDecision -OnlyIfPending $true -Reasons $null).restart
ConvertTo-Json -InputObject $out -Depth 4 -Compress
"""
        r = pwsh(code)
        d = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(d["none"]["reasons"], [])
        self.assertEqual((d["none"]["only"], d["none"]["always"]), (False, True))
        self.assertEqual(d["cbs+wu"]["reasons"], ["component servicing", "Windows Update"])
        self.assertTrue(d["cbs+wu"]["only"])
        self.assertEqual(d["ccm"]["reasons"], ["ConfigMgr client"])
        self.assertEqual(d["ccm-no"]["reasons"], [])
        self.assertEqual(d["rename"]["reasons"], ["computer rename"])
        self.assertEqual((d["pfro"]["reasons"], d["pfro"]["with_pfro"], d["pfro"]["only"]), ([], ["file rename operations"], False))
        self.assertFalse(d["null"])

    def plan(self, tzname, at_utc):
        cfg = {"action": "plan", "at_utc": at_utc, "only_if_pending": True, "count_file_renames": False,
               "task_name": "site-automation restart", "task_script": read(TASK)}
        r = pwsh(read(PS), {"SITE_CHECK_CONFIG": json.dumps(cfg), "TZ": tzname})
        return w.win_restart_parse(r.stdout.splitlines())

    def test_plan_server_time(self):
        # the server's own time zone (TZ here, the Windows zone there) turns the one UTC moment into its local time
        r = self.plan("America/New_York", "2026-11-25T03:30:00Z")
        self.assertNotIn("error", r, r)
        self.assertTrue(r["at_local"].startswith("2026-11-24 22:30 UTC-05:00"), r["at_local"])
        self.assertEqual((r["restart"], r["reasons"], r["why"]), (False, [], "no reboot pending"))
        r = self.plan("Europe/Berlin", "2026-07-01T20:00:00Z")
        self.assertTrue(r["at_local"].startswith("2026-07-01 22:00 UTC+02:00"), r["at_local"])
        r = self.plan("Asia/Kolkata", "2026-11-25T03:30:00Z")
        self.assertTrue(r["at_local"].startswith("2026-11-25 09:00 UTC+05:30"), r["at_local"])

    def test_task_at_restart_time(self):
        tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        try:
            os.makedirs(os.path.join(tmp, "root", "System32"))
            os.makedirs(os.path.join(tmp, "task"))
            fake = os.path.join(tmp, "root", "System32", "shutdown.exe")
            with open(fake, "w") as fh:
                fh.write('#!/bin/sh\necho "$@" > "%s/args.txt"\n' % tmp)
            os.chmod(fake, 0o755)
            shutil.copy(TASK, os.path.join(tmp, "task", "restart-task-20261124-1111.ps1"))
            # another registration's settings beside it: never read by this one
            with open(os.path.join(tmp, "task", "restart-20261124-2222.json"), "w") as fh:
                json.dump({"only_if_pending": False, "comment": "OTHER", "code": "p:0:0", "delay": 1}, fh)

            def run(cfg):
                with open(os.path.join(tmp, "task", "restart-20261124-1111.json"), "w") as fh:
                    json.dump(cfg, fh)
                return subprocess.run(["pwsh", "-NoProfile", "-File", os.path.join(tmp, "task", "restart-task-20261124-1111.ps1")], capture_output=True,
                                      text=True, timeout=120, env=dict(os.environ, SystemRoot=os.path.join(tmp, "root")))
            now_ = datetime.datetime.now(U).strftime("%Y-%m-%dT%H:%M:%SZ")
            later = (datetime.datetime.now(U) + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Run clicked in Task Scheduler an hour early: nothing
            r = run({"only_if_pending": False, "comment": "x", "code": "p:0:0", "delay": 60, "at_utc": later})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "args.txt")))
            os.unlink(os.path.join(tmp, "task", "restart.log"))
            r = run({"only_if_pending": False, "comment": "CHG1: Patch (site-automation, AAP job 7)", "code": "p:0:0", "delay": 60, "at_utc": now_})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(os.path.join(tmp, "args.txt")) as fh:
                self.assertEqual(fh.read().strip(), "/r /t 60 /d p:0:0 /c CHG1: Patch (site-automation, AAP job 7)")
            os.unlink(os.path.join(tmp, "args.txt"))
            # only if pending, nothing pending here: no shutdown, a SKIPPED line
            r = run({"only_if_pending": True, "comment": "x", "code": "p:0:0", "delay": 60, "at_utc": now_})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(tmp, "args.txt")))
            with open(os.path.join(tmp, "task", "restart.log")) as fh:
                self.assertEqual([ln.split(" ", 3)[3] for ln in fh.read().splitlines()],
                                 ["RESTART - restart at the chosen time - CHG1: Patch (site-automation, AAP job 7)", "SKIPPED - no reboot pending"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_task_is_safe(self):
        with open(PS) as fh:
            t = fh.read()
        self.assertIn("<StartWhenAvailable>false</StartWhenAvailable>", t)                             # off at the time: no restart later
        self.assertIn("<EndBoundary>$EndUtc</EndBoundary>", t)
        self.assertIn("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", t)   # the script SYSTEM runs: admins only
        self.assertIn("/setowner '*S-1-5-32-544'", t)
        self.assertIn("<UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel>", t)
        self.assertNotIn("Restart-Computer", t)
        self.assertNotIn("shutdown", re.sub(r"#.*", "", t).lower())        # never a shutdown from the job itself
        # the reason never goes into a command line: only into restart.json
        self.assertNotIn("$cfg.comment +", t)
        self.assertIn("-Arguments ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"' + $script + '\"')", t)


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
class Constrained(unittest.TestCase):
    """Both scripts run in Constrained Language Mode (WDAC or AppLocker on the server), as the other Windows scripts do."""

    def clm(self, script, cfg, after=""):
        code = "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n. '%s'\n%s" % (script.replace("'", "''"), after)
        return pwsh(code, {"SITE_CHECK_CONFIG": json.dumps(cfg), "TZ": "America/New_York"})

    def test_plan(self):
        cfg = {"action": "plan", "at_utc": "2026-11-25T03:30:00Z", "only_if_pending": True, "task_name": "site-automation restart",
               "task_script": read(TASK)}
        r = w.win_restart_parse(self.clm(PS, cfg).stdout.splitlines())
        self.assertNotIn("error", r, r)
        self.assertTrue(r["at_local"].startswith("2026-11-24 22:30 UTC-05:00"), r["at_local"])
        self.assertEqual(r["why"], "no reboot pending")

    def test_task_xml(self):
        cfg = {"action": "list", "task_name": "site-automation restart", "task_script": read(TASK)}
        p = self.clm(PS, cfg, "New-SiteRestartTaskXml -StartUtc '2026-11-25T03:30:00Z' -EndUtc '2026-11-25T04:00:00Z' -Command 'C:\\ps.exe' "
                              "-Arguments '-File \"C:\\x y\\restart-task.ps1\"' -Description 'CHG1: a & b <c> \"d\"'\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        import xml.etree.ElementTree as ET
        xml = p.stdout[p.stdout.index("<?xml"):]
        root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find("t:Triggers/t:TimeTrigger/t:StartBoundary", ns).text, "2026-11-25T03:30:00Z")
        self.assertEqual(root.find("t:Settings/t:StartWhenAvailable", ns).text, "false")
        self.assertEqual(root.find("t:Settings/t:AllowStartOnDemand", ns).text, "false")      # no Run by hand
        self.assertEqual(root.find("t:Settings/t:DeleteExpiredTaskAfter", ns).text, "P1D")
        self.assertEqual(root.find("t:RegistrationInfo/t:Description", ns).text, 'CHG1: a & b <c> "d"')
        self.assertEqual(root.find("t:Actions/t:Exec/t:Arguments", ns).text, '-File "C:\\x y\\restart-task.ps1"')
        self.assertEqual(root.find("t:Principals/t:Principal/t:UserId", ns).text, "S-1-5-18")

    def test_task_script_library(self):
        p = pwsh("$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n$SiteRestartLibrary = $true\n. '%s'\n"
                 "(Get-SiteRestartReasons -Probes (Read-SiteRestartProbes)).Count\n" % TASK)
        self.assertEqual(p.stdout.strip().splitlines()[-1], "0", p.stdout + p.stderr)


# Task Scheduler stand-ins (functions win over commands in PowerShell): a store of tasks by folder + name, built from the
# XML the script registers; switches make a registration fail or read back wrong
STORE = r"""
$global:Store = @{}
$global:FailRegister = $false
$global:BadNext = $false
$global:FailList = $false
$global:Inject = ''
function Get-ScheduledTask {
    [CmdletBinding()] param($TaskPath, $TaskName)
    if ($global:FailList) { throw 'The RPC server is unavailable' }
    if ($TaskName) { return $global:Store[[string]$TaskPath + [string]$TaskName] }
    $global:Store.Values
}
function Register-ScheduledTask {
    [CmdletBinding()] param($Xml, $TaskName, $TaskPath, [switch]$Force)
    $global:Registrations++
    if ($global:Inject) { Invoke-Expression $global:Inject; $global:Inject = '' }       # something else acts right before
    if ($global:FailRegister) { throw 'Access is denied' }
    if (-not $Force -and $global:Store.ContainsKey([string]$TaskPath + [string]$TaskName)) { throw 'Cannot create a file when that file already exists' }
    $x = [xml]$Xml
    $t = [pscustomobject]@{ TaskPath = $TaskPath; TaskName = $TaskName; Author = [string]$x.Task.RegistrationInfo.Author;
                            Description = [string]$x.Task.RegistrationInfo.Description; State = 'Ready'; Xml = $Xml;
                            Actions = @([pscustomobject]@{ Arguments = [string]$x.Task.Actions.Exec.Arguments });
                            Triggers = @([pscustomobject]@{ StartBoundary = [string]$x.Task.Triggers.TimeTrigger.StartBoundary }) }
    $global:Store[[string]$TaskPath + [string]$TaskName] = $t
    $t
}
function Unregister-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath, [switch]$Confirm) $global:Store.Remove([string]$TaskPath + [string]$TaskName) }
function Get-ScheduledTaskInfo {
    [CmdletBinding()] param($TaskName, $TaskPath)
    $t = $global:Store[[string]$TaskPath + [string]$TaskName]
    $n = [datetime]::Parse($t.Triggers[0].StartBoundary).ToLocalTime()
    if ($global:BadNext -and $t.Xml.Contains($global:BadNext)) { $n = $n.AddDays(1) }
    [pscustomobject]@{ NextRunTime = $n }
}
function Export-ScheduledTask { [CmdletBinding()] param($TaskName, $TaskPath) $global:Store[[string]$TaskPath + [string]$TaskName].Xml }
$global:Hardened = @{}
$global:AclCalls = @()
$global:Registrations = 0
function whoami.exe { $global:LASTEXITCODE = 0; '"admin","S-1-5-21-1000"' }
function Get-Acl { param($LiteralPath) @{ Sddl = $LiteralPath } }
function ConvertFrom-SddlString {
    param($Sddl)
    $dir = Test-Path -LiteralPath $Sddl -PathType Container
    $owner = 'S-1-5-32-544'
    $flags = 16
    $control = 0
    if ($dir) {
        $flags = 3
        if ($global:Hardened[$Sddl]) { $control = 0x1000 }
        elseif ($global:UnsafeDirOwner) { $owner = 'S-1-5-21-2000' }
    }
    $aces = @(foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        @{ AceFlags = $flags; AceType = 'AccessAllowed'; AccessMask = 0x1f01ff; SecurityIdentifier = @{ Value = $sid } }
    })
    if ($global:LegacyParent -eq $Sddl -and -not $control) {
        $aces += @{ AceFlags = 2; AceType = 'AccessAllowed'; AccessMask = 0x106; SecurityIdentifier = @{ Value = 'S-1-5-32-545' } }
    }
    if (-not $dir -and $Sddl -like '*.ps1') {
        if ($global:BadOwner) { $owner = 'S-1-5-21-2000' }
        if ($global:BadGrant) { $aces += @{ AceType = 'AccessAllowed'; AccessMask = 2; SecurityIdentifier = @{ Value = 'S-1-1-0' } } }
    }
    @{ RawDescriptor = @{ Owner = @{ Value = $owner }; ControlFlags = $control; DiscretionaryAcl = $aces } }
}
function icacls.exe {
    param($Path, $Operation, $Value, $Link)
    $global:AclCalls += "$Operation $Path"
    $global:LASTEXITCODE = 0
    if ($Operation -eq $global:FailAcl -or ($global:FailScriptAcl -and $Path -like '*.ps1') -or $Link -ne '/L') { $global:LASTEXITCODE = 5; return }
    if ($Operation -eq '/restore') {
        $leaf, $dacl = Get-Content -LiteralPath $Value -Encoding Unicode
        if ($dacl -ne 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)') { throw 'unexpected policy' }
        $global:Hardened[(Join-Path $Path $leaf)] = $true
    }
}
function Move-Item {
    param($LiteralPath, $Destination, $ErrorAction)
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -ErrorAction Stop
    $global:Hardened[(Join-Path $Destination (Split-Path -Leaf $LiteralPath))] = $global:Hardened[$LiteralPath]
}
function Step($cfg) {
    $env:SITE_CHECK_CONFIG = ($cfg | ConvertTo-Json -Compress)
    $line = (. $global:PS | Select-Object -Last 1)
    $o = ConvertFrom-Json ($line -replace '^###SITE-JSON### ', '')
    $files = @(Get-ChildItem -Path $cfg.dir -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name } | Sort-Object)
    $t = $global:Store.Values | Select-Object -First 1
    $saved = ''
    if ($o.scheduled) {
        $settingsFile = Join-Path $cfg.dir ('restart-' + $o.scheduled.id + '.json')
        $saved = Get-Content -LiteralPath $settingsFile -Raw
    }
    $args_ = ''
    if ($null -ne $t) { $args_ = [string]$t.Actions[0].Arguments }
    Write-Output ('STEP ' + (@{ out = $o; files = $files; task = $args_; author = [string]$t.Author; saved = $saved; registrations = $global:Registrations; acl = $global:AclCalls } | ConvertTo-Json -Compress -Depth 6))
}
"""


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
class Registration(unittest.TestCase):
    """The schedule and cancel actions against stand-in Task Scheduler cmdlets: an earlier restart removed on a skip; another
    product's task of our name never touched; each registration its own files, the earlier ones kept until the new task reads
    back right; a failed read-back or registration rolled back, and said."""

    def run_steps(self, steps):
        tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        try:
            d = os.path.join(tmp, "site-automation", "restart")
            os.makedirs(os.path.join(tmp, "root"))
            at_ = (datetime.datetime.now(U) + datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:00Z")
            at2 = (datetime.datetime.now(U) + datetime.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:00Z")
            base = {"task_path": "\\site-automation\\", "task_name": "site-automation restart", "dir": d, "code": "p:0:0", "delay": 60,
                    "task_script": read(TASK), "count_file_renames": False}
            code = ["$global:PS = '%s'" % PS.replace("'", "''"), STORE]
            for st in steps:
                if isinstance(st, str):
                    code.append(st.replace("{dir}", d))
                    continue
                cfg = dict(base, **st)
                cfg["at_utc"] = at2 if cfg.pop("later", False) else at_
                cfg.setdefault("comment", "CHG1: Patch (site-automation, AAP job 7)")
                cfg["description"] = cfg["comment"]
                code.append("Step (ConvertFrom-Json '%s')" % json.dumps(cfg).replace("'", "''"))
            r = pwsh("\n".join(code), {"SystemRoot": os.path.join(tmp, "root"), "ProgramData": tmp, "TEMP": os.path.join(tmp, "root"), "TZ": "America/New_York"})
            out = [json.loads(x[5:]) for x in r.stdout.splitlines() if x.startswith("STEP ")]
            self.assertEqual(len(out), len([x for x in steps if not isinstance(x, str)]), r.stdout + r.stderr)
            return out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_skip_removes_our_earlier_restart(self):
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False}, {"action": "schedule", "only_if_pending": True}])
        self.assertTrue(a["out"]["scheduled"])
        self.assertTrue(any(f.startswith("restart-task-") for f in a["files"]))
        self.assertFalse(b["out"]["restart"])
        self.assertEqual(len(b["out"]["removed"]), 1)                 # the earlier unconditional restart: gone
        self.assertEqual((b["task"], b["files"]), ("", []))
        m = w.win_restart_model([{"host": "h", "result": b["out"]}], {"mode": "schedule"})
        self.assertIn("was removed", w.win_restart_doc(m, WHEN)["sections"][0]["rows"][0][5])

    def test_ours_carry_their_restart_moment(self):
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False}, {"action": "list"}])
        # its settings' restart moment (only to say an unmanage window there is likely that restart's): 2 hours ahead here
        saved = json.loads(a["saved"])["at_utc"]
        self.assertRegex(saved, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(b["out"]["ours"][0]["at_utc"], saved)
        self.assertLess(abs((rs.parse(saved) - datetime.datetime.now(U)).total_seconds() - 7200), 180)

    def test_task_path_normalized_for_readback_and_cancel(self):
        for path, expected in (("", "\\"), ("\\", "\\"), ("site-automation", "\\site-automation\\")):
            with self.subTest(path=path):
                a, b = self.run_steps([{"action": "schedule", "task_path": path}, {"action": "cancel", "task_path": expected}])
                self.assertEqual(a["out"]["scheduled"]["path"], expected)
                self.assertEqual(len(b["out"]["removed"]), 1)
                self.assertEqual((b["task"], b["files"]), ("", []))

    @unittest.skipUnless(os.name == "nt", "requires Windows NTFS ACLs")
    def test_native_directory_and_script_acl(self):
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            self.skipTest("requires an elevated Windows test process")
        with tempfile.TemporaryDirectory() as root:
            cfg = {"action": "none", "task_name": "SRV-APP01", "task_script": read(TASK)}
            code = "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n. '%s'\n" % PS.replace("'", "''") + r"""
$ErrorActionPreference = 'Stop'
$parent = Join-Path $env:ProgramData 'site-automation'
$dir = Join-Path $parent 'restart'
Initialize-SiteRestartDir $dir
$file = New-Item -Path (Join-Path $dir 'restart-task-1-1.ps1') -ItemType File
$null = & icacls.exe $file.FullName /setowner '*S-1-5-32-544' /L
if ($LASTEXITCODE -ne 0) { throw 'could not set test script owner' }
Assert-SiteRestartFileAcl $file.FullName
foreach ($path in @($parent, $dir)) {
    if (-not (Test-SitePrivateAcl (Get-SiteRestartAcl $path) -Directory)) { throw "unexpected ACL on $path" }
}
'ACL verified'
"""
            proc = pwsh(code, {"ProgramData": root, "SITE_CHECK_CONFIG": json.dumps(cfg)})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("ACL verified", proc.stdout)

    def test_harden_parent_then_restart_directory(self):
        (a,) = self.run_steps([{"action": "schedule"}])
        self.assertTrue(a["out"].get("scheduled"), a)
        self.assertEqual([op.split()[0] for op in a["acl"]], ["/setowner", "/restore", "/setowner", "/restore", "/setowner"])
        self.assertTrue(a["acl"][0].endswith("site-automation"))
        self.assertTrue(a["acl"][2].endswith("restart"))

    def test_existing_parent_with_legacy_protected_child(self):
        (a,) = self.run_steps(["$null = New-Item -ItemType Directory -Path '{dir}' -Force; $global:Hardened['{dir}'] = $true; "
                               "$global:LegacyParent = Split-Path -Parent '{dir}'", {"action": "schedule"}])
        self.assertTrue(a["out"].get("scheduled"), a)
        self.assertTrue(a["acl"][0].endswith("site-automation"))
        self.assertEqual(len(a["acl"]), 3)

    def test_hardening_failures_prevent_registration(self):
        for flag, why in (("$global:FailAcl = '/setowner'", "/setowner"), ("$global:FailAcl = '/restore'", "/restore"),
                          ("New-Item -ItemType Directory -Path (Split-Path -Parent '{dir}') | Out-Null; $global:UnsafeDirOwner = $true",
                           "Unsafe existing owner"),
                          ("$global:FailScriptAcl = $true", "/setowner"),
                          ("$global:BadOwner = $true", "Unexpected owner or ACL"), ("$global:BadGrant = $true", "Unexpected owner or ACL")):
            with self.subTest(flag=flag):
                (a,) = self.run_steps([flag, {"action": "schedule"}])
                self.assertIn(why, a["out"]["error"])
                self.assertEqual((a["registrations"], a["files"]), (0, []))

    def test_reparse_point_refused_at_either_level(self):
        for link in ("(Split-Path -Parent '{dir}')", "'{dir}'"):
            with self.subTest(link=link):
                (a,) = self.run_steps(["$p = " + link + "; $null = New-Item -ItemType Directory -Path (Split-Path -Parent $p) -Force; "
                                       "$null = New-Item -ItemType SymbolicLink -Path $p -Target $env:SystemRoot", {"action": "schedule"}])
                self.assertIn("reparse point", a["out"]["error"])
                self.assertEqual((a["registrations"], a["files"]), (0, []))

    def test_own_files_and_replace(self):
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False}, {"action": "schedule", "only_if_pending": False, "later": True}])
        ida, idb = a["out"]["scheduled"]["id"], b["out"]["scheduled"]["id"]
        self.assertNotEqual(ida, idb)
        self.assertIn("restart-task-%s.ps1" % idb, b["task"])
        self.assertEqual(b["files"], ["restart-%s.json" % idb, "restart-task-%s.ps1" % idb])   # the earlier's removed after
        self.assertEqual(len(b["out"]["replaced"]), 1)

    def test_rollback_on_bad_read_back(self):
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False},
                               "$global:BadNext = 'AAP job 8'",
                               {"action": "schedule", "only_if_pending": False, "later": True, "comment": "CHG2: x (site-automation, AAP job 8)"}])
        ida = a["out"]["scheduled"]["id"]
        self.assertIn("rolled back: the earlier restart", b["out"]["error"])
        self.assertIn("restart-task-%s.ps1" % ida, b["task"])          # the earlier task is back, with its own files
        self.assertEqual(b["files"], ["restart-%s.json" % ida, "restart-task-%s.ps1" % ida])

    def test_rollback_when_registration_fails(self):
        (a,) = self.run_steps(["$global:FailRegister = $true", {"action": "schedule", "only_if_pending": False}])
        self.assertIn("nothing this run registered is there: left as it is", a["out"]["error"])
        self.assertEqual((a["task"], a["files"]), ("", []))

    def test_rollback_new_task_when_none_before(self):
        (a,) = self.run_steps(["$global:BadNext = 'AAP job 7'", {"action": "schedule", "only_if_pending": False}])
        self.assertIn("rolled back: no restart of ours is scheduled", a["out"]["error"])
        self.assertEqual((a["task"], a["files"]), ("", []))

    def test_another_products_task_is_never_touched(self):
        foreign = ("$global:Store['\\site-automation\\site-automation restart'] = [pscustomobject]@{ TaskPath = '\\site-automation\\'; "
                   "TaskName = 'site-automation restart'; Author = 'OtherVendor'; Description = 'their job'; State = 'Ready'; Xml = '<x/>'; "
                   "Actions = @([pscustomobject]@{ Arguments = '-File C:\\other\\run.ps1' }); Triggers = @() }")
        a, b, c = self.run_steps([foreign, {"action": "plan", "only_if_pending": False}, {"action": "schedule", "only_if_pending": False},
                                  {"action": "cancel"}])
        for x in (a, b, c):
            self.assertIn("did not make", x["out"]["error"])
            self.assertEqual(x["author"], "OtherVendor")
        self.assertEqual(b["files"], [])

    def test_vendor_task_made_meanwhile_is_left(self):
        foreign = ("$global:Store['\\site-automation\\site-automation restart'] = [pscustomobject]@{ TaskPath = '\\site-automation\\'; "
                   "TaskName = 'site-automation restart'; Author = 'OtherVendor'; Description = 'theirs'; State = 'Ready'; Xml = '<x/>'; "
                   "Actions = @([pscustomobject]@{ Arguments = '-File C:\\other\\run.ps1' }); Triggers = @() }")
        (a,) = self.run_steps(['$global:Inject = "%s"' % foreign.replace('"', '`"').replace("$", "`$"), {"action": "schedule", "only_if_pending": False}])
        self.assertIn("nothing this run registered is there: left as it is", a["out"]["error"])
        self.assertEqual(a["author"], "OtherVendor")                  # never overwritten, never deleted
        self.assertEqual(a["files"], [])

    def test_lookup_error_stops(self):
        (a,) = self.run_steps(["$global:FailList = $true", {"action": "schedule", "only_if_pending": False}])
        self.assertIn("RPC server is unavailable", a["out"]["error"])
        self.assertEqual((a["task"], a["files"]), ("", []))

    def test_lock(self):
        a, b = self.run_steps(["New-Item -ItemType Directory -Path '{dir}' -Force | Out-Null; New-Item -ItemType File -Path (Join-Path '{dir}' 'restart.lock') | Out-Null",
                               {"action": "schedule", "only_if_pending": False},
                               "(Get-Item (Join-Path '{dir}' 'restart.lock')).LastWriteTime = (Get-Date).AddMinutes(-20)",
                               {"action": "schedule", "only_if_pending": False}])
        self.assertIn("another schedule or cancel", a["out"]["error"])
        self.assertEqual(a["task"], "")
        self.assertTrue(b["out"]["scheduled"])                         # a lock older than 10 minutes is left over
        self.assertNotIn("restart.lock", b["files"])

    def test_only_the_replaced_registrations_files(self):
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False},
                               "Set-Content -Path (Join-Path '{dir}' 'restart-task-20990101000000-1234.ps1') -Value 'x'",
                               {"action": "schedule", "only_if_pending": False, "later": True}])
        idb = b["out"]["scheduled"]["id"]
        self.assertEqual(b["files"], sorted(["restart-%s.json" % idb, "restart-task-%s.ps1" % idb, "restart-task-20990101000000-1234.ps1"]))

    def test_cancel(self):
        a, b, c = self.run_steps([{"action": "schedule", "only_if_pending": False}, {"action": "list"}, {"action": "cancel"}])
        self.assertEqual(len(b["out"]["ours"]), 1)
        self.assertEqual(len(c["out"]["removed"]), 1)
        self.assertEqual((c["task"], c["files"]), ("", []))

    def test_ours_needs_all_three_marks(self):
        code = ". '%s'\n" % PS.replace("'", "''") + r"""
$d = Join-Path ([IO.Path]::GetTempPath()) 'restart'
$ok = '-NoProfile -File "' + (Join-Path $d 'restart-task-1-1.ps1') + '"'
function T($author, $desc, $args_) { Test-SiteOurTask -Task ([pscustomobject]@{ Author = $author; Description = $desc; Actions = @([pscustomobject]@{ Arguments = $args_ }) }) -Dir $d }
'MARKS ' + (@((T 'site-automation' 'x (site-automation, AAP job 1)' $ok), (T 'Other' 'x (site-automation)' $ok), (T 'site-automation' 'x' $ok),
  (T 'site-automation' 'x (site-automation)' '-File "/other/restart-task-1.ps1"')) -join ',')
"""
        r = pwsh(code, {"SITE_CHECK_CONFIG": json.dumps({"action": "none", "task_name": "x", "task_path": "\\x\\", "dir": "/nonexistent",
                                                         "task_script": read(TASK)})})
        self.assertIn("MARKS True,False,False,False", r.stdout, r.stdout + r.stderr)


sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_win_restart_swis as rs  # noqa: E402


ORION = "https://orion.example.mil:17774"


class FakeSwis(object):
    """Orion.Nodes as SWIS answers it: SELECTs by name, DNS, IP or NodeID; Unmanage recorded (or failing); Remanage must
    never be asked for. zone_local: the window read back in that zone, without one. before_read: {node_id: fn} run before
    that node is read by its ID (a change meanwhile)."""

    url = ORION

    def __init__(self, nodes, fail=None, zone_local=None, before_read=None):
        self.nodes, self.calls, self.fail, self.zone_local, self.before_read = nodes, [], fail, zone_local, before_read or {}

    def query(self, swql, params=None):
        p = dict((k, str(v).lower()) for k, v in (params or {}).items())
        if "NodeID = @id" in swql:
            nid = int(params["id"])
            if nid in self.before_read:
                self.before_read.pop(nid)(self.nodes)
            return [dict(n) for n in self.nodes if int(n["NodeID"]) == nid]
        out = []
        for n in self.nodes:
            cap, dns, ip = str(n["Caption"]).lower(), str(n.get("DNS") or "").lower(), str(n.get("IPAddress") or "")
            if (cap in (p.get("short"), p.get("full")) or dns in (p.get("short"), p.get("full")) or ip == p.get("ip")
                    or cap.startswith(p.get("short", "#") + ".") or dns.startswith(p.get("short", "#") + ".")):
                out.append(dict(n))
        return out

    def invoke(self, verb, args):
        assert verb != "Remanage", "Remanage must never be called"
        if self.fail:
            raise rs.sw.SwisError(self.fail, "auth")
        self.calls.append([verb] + list(args))
        nid = int(str(args[0]).split(":")[1])
        n = next(x for x in self.nodes if int(x["NodeID"]) == nid)
        f, u = rs.parse(args[1]), rs.parse(args[2])
        if self.zone_local is not None:
            f, u = f.astimezone(self.zone_local), u.astimezone(self.zone_local)
        n.update(UnManageFrom=f.strftime("%Y-%m-%dT%H:%M:%S"), UnManageUntil=u.strftime("%Y-%m-%dT%H:%M:%S"), UnManaged=False)
        return True


def orion():
    return [{"NodeID": 412, "Caption": "SRV-APP01", "DNS": "srv-app01.corp.example.mil", "IPAddress": "192.0.2.10"},
            {"NodeID": 418, "Caption": "SRV-DB01", "DNS": "srv-db01.corp.example.mil", "IPAddress": "192.0.2.11"},
            {"NodeID": 501, "Caption": "srv-web02", "DNS": "", "IPAddress": "192.0.2.20"},
            {"NodeID": 502, "Caption": "SRV-WEB02", "DNS": "", "IPAddress": "192.0.2.21"},
            {"NodeID": 600, "Caption": "SQL-CLUSTER-A", "DNS": "", "IPAddress": "192.0.2.30"},
            {"NodeID": 700, "Caption": "srv-mail01", "DNS": "srv-mail01.other.example.mil", "IPAddress": "192.0.2.40"},
            {"NodeID": 800, "Caption": "SRV-FS01", "DNS": "", "IPAddress": "192.0.2.50"}]


NOW = at(2026, 11, 24, 15, 0)
WIN = rs.window("2026-11-25T03:30:00Z", 5, 10)


APP = "srv-app01.corp.example.mil"
AT = "2026-11-25T03:30:00Z"


def item(host=APP, **k):
    return dict({"host": host, "ip": "", "window": WIN, "earlier": {}}, **k)


def run_(c, items, check=False, tz=None, now=NOW, at_=AT, **k):
    return rs.run(c, items, at_, check, tz, clock=k.pop("clock", lambda: now), **k)


class SolarWinds(unittest.TestCase):
    def test_window_in_utc(self):
        self.assertEqual(WIN, {"from": "2026-11-25T03:25:00Z", "until": "2026-11-25T03:40:00Z"})
        self.assertEqual(rs.window("2026-11-25T03:30:00Z"), WIN)                    # the defaults: 5 before, 10 after
        import zoneinfo
        self.assertEqual(rs.shown(WIN, zoneinfo.ZoneInfo("America/New_York")), "22:25-22:40 EST")
        self.assertEqual(rs.shown(rs.window("2026-11-25T04:55:00Z", 5, 10), zoneinfo.ZoneInfo("America/New_York")), "23:50-2026-11-25 00:05 EST")

    def test_read_back_tells_the_convention_only(self):
        import zoneinfo
        ber = zoneinfo.ZoneInfo("Europe/Berlin")
        win = rs.window("2026-11-24T21:30:00Z")
        self.assertEqual(rs.same({"from": "2026-11-24T21:25:00.0000000", "until": "2026-11-24T21:40:00"}, win, ber), "utc")
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00", "until": "2026-11-24T22:40:00"}, win, ber), "local")
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00Z", "until": "2026-11-24T22:40:00Z"}, win, ber), "")
        c = FakeSwis(orion(), zone_local=ber)
        res = run_(c, [item(window=win)], tz=ber, now=at(2026, 11, 24, 15), at_="2026-11-24T21:30:00Z")
        self.assertEqual((res[APP]["times"], res[APP]["done"]), ("local", True))
        m = w.win_restart_model([], {"mode": "schedule", "swis_window": "x", "swis_times": ["local"]})
        self.assertIn("SolarWinds returns times as local time", " ".join(w.win_restart_doc(m, WHEN)["sections"][1]["lines"]))

    def test_find_by_name(self):
        f = rs.find(FakeSwis(orion()), [
            {"host": APP, "ip": "192.0.2.10"},                                    # full name
            {"host": "srv-web02", "ip": "192.0.2.20"},                            # two nodes named so: refused, the IP never decides
            {"host": "srv-gone.corp.example.mil", "ip": ""},
            {"host": "sql01.corp.example.mil", "ip": "192.0.2.30"},               # only an IP (a cluster address)
            {"host": "srv-mail01.corp.example.mil", "ip": ""},                    # its short name's node is of another domain
            {"host": "srv-fs01.corp.example.mil", "ip": ""}])                     # the node has no domain: the short name will do
        self.assertEqual((f[APP]["node"]["node_id"], f[APP]["name"]), (412, APP))
        self.assertIsNone(f["srv-web02"]["node"])
        self.assertEqual(f["srv-web02"]["why"], "2 SolarWinds nodes are named srv-web02")
        self.assertIsNone(f["srv-gone.corp.example.mil"]["node"])
        self.assertIn("matched by IP only (SQL-CLUSTER-A N:600) - not unmanaged", f["sql01.corp.example.mil"]["why"])
        self.assertIsNone(f["srv-mail01.corp.example.mil"]["node"])
        self.assertIn("a SolarWinds node named srv-mail01 is srv-mail01.other.example.mil", f["srv-mail01.corp.example.mil"]["why"])
        self.assertEqual(f["srv-fs01.corp.example.mil"]["node"]["node_id"], 800)
        f = rs.find(FakeSwis(orion()), [{"host": "srv-mail01", "ip": ""}])     # a server without a domain: its one node
        self.assertEqual(f["srv-mail01"]["node"]["node_id"], 700)
        f = rs.find(FakeSwis(orion()), [{"host": "srv-fs01", "ip": ""}, {"host": "srv-fs01.corp.example.mil", "ip": ""}])
        self.assertTrue(all(v["node"] is None and "2 servers of this job" in v["why"] for v in f.values()))

    def test_unmanage_call(self):
        c = FakeSwis(orion())
        res = run_(c, [item()])
        self.assertEqual(c.calls, [["Unmanage", "N:412", "2026-11-25T03:25:00Z", "2026-11-25T03:40:00Z", False]])
        self.assertEqual((res[APP]["done"], res[APP]["times"]), (True, "utc"))
        self.assertIn("node SRV-APP01 (N:412) unmanaged", res[APP]["text"])
        c = FakeSwis(orion())
        res = run_(c, [item()], check=True)
        self.assertEqual(c.calls, [])
        self.assertIn("would be unmanaged", res[APP]["text"])
        res = run_(FakeSwis(orion()), [item("srv-web02")])
        self.assertEqual((res["srv-web02"]["level"], res["srv-web02"]["done"]), ("warning", False))
        self.assertIn("node not found (2 SolarWinds nodes are named srv-web02) - not unmanaged", res["srv-web02"]["text"])

    def test_any_window_is_left_alone(self):
        cases = [dict(UnManaged=True),                                                                # unmanaged now
                 dict(UnManageFrom="2026-11-25T03:25:00", UnManageUntil="2026-11-25T03:40:00"),         # the very window asked for
                 dict(UnManageFrom="2026-11-24T10:00:00", UnManageUntil="2026-11-24T23:00:00"),         # a person's, ahead
                 dict(UnManageFrom="2026-11-24T10:00:00", UnManageUntil="2026-11-24T15:30:00"),         # past as UTC, ahead as Berlin
                 dict(UnManageFrom="2026-11-24T10:00:00", UnManageUntil="tomorrow at noon")]            # an end not readable
        import zoneinfo
        for case in cases:
            nodes = orion()
            nodes[0].update(case)
            c = FakeSwis(nodes)
            res = run_(c, [item()], tz=zoneinfo.ZoneInfo("Europe/Berlin"))
            self.assertEqual((c.calls, res[APP]["level"]), ([], "warning"), case)
            self.assertIn("already has an unmanage window", res[APP]["text"])
            self.assertIn("left as it is", res[APP]["text"])
        nodes = orion()
        nodes[0].update(UnManageFrom="2026-11-23T10:00:00", UnManageUntil="2026-11-23T11:00:00")   # over every way
        c = FakeSwis(nodes)
        run_(c, [item()], tz=zoneinfo.ZoneInfo("Europe/Berlin"))
        self.assertEqual(len(c.calls), 1)

    def test_naive_end_fourteen_hour_boundary(self):
        for end, preserved in (("01:00:01", True), ("01:00:00", False), ("14:59:59Z", False), ("14:59:59-01:00", True)):
            nodes = orion()
            nodes[0].update(UnManageUntil="2026-11-24T" + end)
            client = FakeSwis(nodes)
            result = run_(client, [item()])[APP]
            self.assertEqual(bool(client.calls), not preserved, end)
            self.assertEqual("left as it is" in result["text"], preserved, end)

    def test_fall_back_second_fold_is_still_ahead(self):
        # New York, 2026-11-01 at 06:15 UTC (01:15 EST, the second 01:xx): an end 01:45 without a zone - the first 01:45
        # (05:45 UTC) has passed, the second (06:45 UTC) has not: a window, left
        import zoneinfo
        ny = zoneinfo.ZoneInfo("America/New_York")
        now = at(2026, 11, 1, 6, 15)
        self.assertTrue(rs.has_window({"until": "2026-11-01T01:45:00"}, now, ny))
        self.assertTrue(rs.has_window({"until": "2026-11-01T01:45:00"}, at(2026, 11, 1, 6, 50), ny))
        self.assertTrue(rs.has_window({"until": "2026-11-01T01:45:00Z"}, at(2026, 11, 1, 1, 0), ny))
        self.assertFalse(rs.has_window({"until": "2026-11-01T01:45:00Z"}, now, ny))     # with a zone: exactly that
        self.assertTrue(rs.has_window({"until": "not a time"}, now, ny))
        self.assertFalse(rs.has_window({"until": ""}, now, ny))

    def test_reschedule_says_the_old_window_ends_by_itself(self):
        nodes = orion()
        nodes[0].update(UnManageFrom="2026-11-24T22:55:00", UnManageUntil="2026-11-24T23:10:00")
        c = FakeSwis(nodes)
        earlier = rs.window("2026-11-24T23:00:00Z")
        res = run_(c, [item(earlier=earlier)])
        self.assertEqual(c.calls, [])
        self.assertIn("likely the replaced restart's (it ends by itself) - left as it is", res[APP]["text"])
        hosts = [{"host": "a", "ip": "192.0.2.10", "result": {"restart": True, "scheduled": {"id": "2"}, "replaced": [{"at_utc": "2026-11-24T23:00:00Z"}]}}]
        self.assertEqual(w.win_restart_swis_items(hosts, "schedule")[0], {"host": "a", "ip": "192.0.2.10", "earlier": earlier})

    def test_node_checked_again_before_the_write(self):
        def rename(ns):
            ns[0].update(Caption="SRV-OTHER", DNS="srv-other.corp.example.mil")
        c = FakeSwis(orion(), before_read={412: rename})
        res = run_(c, [item()])
        self.assertEqual(c.calls, [])
        self.assertIn("node N:412 is not named srv-app01.corp.example.mil any more - not unmanaged", res[APP]["text"])
        # matched by its short caption, then it gains a DNS name of another domain: no write
        def other_domain(ns):
            ns[6].update(DNS="srv-fs01.other.example.mil")
        c = FakeSwis(orion(), before_read={800: other_domain})
        res = run_(c, [item("srv-fs01.corp.example.mil")])
        self.assertEqual(c.calls, [])
        self.assertEqual(res["srv-fs01.corp.example.mil"]["level"], "warning")
        self.assertIn("node N:800 is now named srv-fs01.other.example.mil, not srv-fs01.corp.example.mil", res["srv-fs01.corp.example.mil"]["text"])

    def test_bounded_and_never_late(self):
        # the restart less than a minute away by the time SolarWinds comes: skipped, a warning
        c = FakeSwis(orion())
        res = run_(c, [item()], now=at(2026, 11, 25, 3, 29, 30))
        self.assertEqual(c.calls, [])
        self.assertIn("less than a minute away", res[APP]["text"])
        # past the budget: the rest skipped (each lookup here "takes" 50 s)
        t = [NOW]

        def clock():
            t[0] = t[0] + datetime.timedelta(seconds=50)
            return t[0]
        c = FakeSwis(orion())
        res = run_(c, [item(), item("srv-fs01.corp.example.mil"), item("srv-db01.corp.example.mil")], clock=clock, budget=120)
        self.assertEqual(res["srv-db01.corp.example.mil"]["level"], "warning")
        self.assertTrue(any("took longer than 120s" in r["text"] or "took too long" in r["text"] for r in res.values()))
        self.assertLess(len(c.calls), 3)

    def test_read_back_failure_keeps_done(self):
        class Flaky(FakeSwis):
            def query(self, swql, params=None):
                if "NodeID = @id" in swql and self.calls:
                    raise rs.sw.SwisError("no answer in time", "timeout")
                return FakeSwis.query(self, swql, params)
        c = Flaky(orion())
        res = run_(c, [item()])
        self.assertEqual(len(c.calls), 1)
        self.assertTrue(res[APP]["done"])
        self.assertEqual(res[APP]["level"], "warning")
        self.assertIn("unmanaged 22:25-22:40" if False else "unmanaged", res[APP]["text"])
        self.assertIn("; the read-back failed (no answer in time)", res[APP]["text"])
        self.assertNotIn("unmanage failed", res[APP]["text"])

    def test_no_remanage_anywhere(self):
        for p in (os.path.join(REPO, "plugins", "module_utils", "site_win_restart_swis.py"),
                  os.path.join(REPO, "roles", "win_restart", "library", "site_win_restart_swis.py"),
                  os.path.join(REPO, "plugins", "filter", "win_restart_filters.py")):
            with open(p) as fh:
                self.assertNotIn('invoke("Remanage"', fh.read(), p)
        self.assertEqual(rs.VERBS, ("Unmanage",))
        self.assertEqual(w.win_restart_swis_items([{"host": "c", "result": {"restart": False, "removed": [{"at_utc": AT}]}}], "schedule"), [])
        self.assertEqual(w.win_restart_swis_items([{"host": "c", "result": {"removed": [{"at_utc": AT}]}}], "cancel"), [])
        with open(os.path.join(REPO, "playbooks", "win_restart_cancel.yml")) as fh:
            self.assertNotIn("site_win_restart_swis", fh.read())
        mp = w.win_restart_swis_cancel([{"host": "c", "result": {"removed": [{"at_utc": AT}]}}], False, "America/New_York")
        self.assertEqual(mp["c"]["text"], "SolarWinds: the unmanage window 22:25-22:40 EST (if it was set) stays and ends by itself; "
                                          "re-manage the node by hand in SolarWinds if needed")
        mp = w.win_restart_swis_map([{"host": "c", "result": {"restart": False, "removed": [{"at_utc": AT}]}}], {}, "on", "", "America/New_York")
        self.assertIn("not unmanaged (skipped: no reboot pending); the earlier restart's unmanage window (22:25-22:40 EST)", mp["c"]["text"])

    def test_a_failed_call_is_a_warning(self):
        res = run_(FakeSwis(orion(), fail="SolarWinds refused it (HTTP 403): the account may not manage nodes"), [item()])
        self.assertEqual((res[APP]["level"], res[APP]["done"]), ("warning", False))
        self.assertIn("unmanage failed (SolarWinds refused it (HTTP 403)", res[APP]["text"])

    def test_invoke_request(self):
        sent = []

        class Opener(object):
            def open(self, req, timeout=None):
                sent.append((req.full_url, json.loads(req.data.decode()), req.get_method()))

                class R(object):
                    def read(self, n):
                        return b"null"
                return R()
        m = rs.Manager("orion.example.mil", "u", "p", opener=Opener())
        m.invoke("Unmanage", ["N:412", WIN["from"], WIN["until"], False])
        self.assertEqual(sent[0], (ORION + "/SolarWinds/InformationService/v3/Json/Invoke/Orion.Nodes/Unmanage",
                                   ["N:412", "2026-11-25T03:25:00Z", "2026-11-25T03:40:00Z", False], "POST"))
        for verb in ("Remanage", "Delete"):
            with self.assertRaises(rs.sw.SwisError):
                m.invoke(verb, ["N:412"])
        self.assertEqual(len(sent), 1)
        with self.assertRaises(rs.sw.SwisError):            # the read-only client's SELECT-only rule still holds
            m.query("DELETE FROM Orion.Nodes")

    def test_writes_never_take_the_read_only_credential(self):
        lib = os.path.join(REPO, "roles", "win_restart", "library", "site_win_restart_swis.py")
        with open(lib) as fh:
            code = re.sub(r'""".*?"""', "", fh.read(), flags=re.S)
        self.assertNotIn('"SOLARWINDS_URL"', code)
        self.assertNotIn('"SOLARWINDS_USERNAME"', code)
        self.assertIn('"SOLARWINDS_MANAGE_USERNAME"', code)
        self.assertIn("retries=0", code)                                           # no retry spends the lead time
        import yaml
        with open(os.path.join(REPO, "aap", "credential_types", "solarwinds_manage.yml")) as fh:
            ct = yaml.safe_load(fh)
        self.assertEqual(sorted(ct["injectors"]["env"]), ["SOLARWINDS_MANAGE_CA_PATH", "SOLARWINDS_MANAGE_PASSWORD", "SOLARWINDS_MANAGE_URL",
                                                          "SOLARWINDS_MANAGE_USERNAME", "SOLARWINDS_MANAGE_VERIFY"])

    def test_one_run_at_a_time(self):
        import yaml
        with open(os.path.join(REPO, "aap", "job_templates.yml")) as fh:
            jt = dict((e["name"], e) for e in yaml.safe_load(fh)["job_templates"])
        self.assertIs(jt["Windows - schedule a restart"]["allow_simultaneous"], False)
        self.assertIn("aap", jt["Windows - schedule a restart"]["optional_credentials"])
        with open(os.path.join(REPO, "roles", "aap_templates", "library", "site_aap_templates.py")) as fh:
            self.assertIn('fields["allow_simultaneous"] = bool(e["allow_simultaneous"])', fh.read())
        with open(os.path.join(REPO, "playbooks", "win_restart_schedule.yml")) as fh:
            t = fh.read()
        self.assertLess(t.index("site_win_restart_busy:"), t.index("name: The plan (Check) or the restart scheduled"))
        # the restart tasks first, SolarWinds after
        self.assertLess(t.index("name: The plan (Check) or the restart scheduled"), t.index("site_win_restart_swis:"))
        with open(os.path.join(REPO, "roles", "win_restart", "library", "site_win_restart_busy.py")) as fh:
            t = fh.read()
        self.assertIn("status__in=pending,waiting,running", t)          # the playbook runs it in every e2e run below (no AAP: said)

    def test_busy_job_order_and_pagination(self):
        from unittest.mock import Mock, patch
        import site_aap
        import importlib.util
        spec = importlib.util.spec_from_file_location("busy", os.path.join(REPO, "roles", "win_restart", "library", "site_win_restart_busy.py"))
        busy = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"ansible.module_utils.site_aap": site_aap}):
            spec.loader.exec_module(busy)
        module = Mock(params={"job_id": 100, "template_id": 1, "ca_path": "", "timeout": 20})
        api = Mock()
        api.probe.return_value = ""
        api.call.side_effect = [(200, {"results": [{"id": 100, "status": "running"}, {"id": 101, "status": "waiting"}]}),
                                (200, {"next": True, "results": [{"id": 100, "status": "running"}, {"id": 101, "status": "waiting"},
                                                                  {"id": 102, "status": "pending"}]}),
                                (200, {"results": [{"id": 103, "status": "running"}]}),
                                (200, {"results": [{"id": 97, "status": "running"}, {"id": 98, "status": "pending"},
                                                   {"id": 99, "status": "waiting"}, {"id": 101, "status": "waiting"}]})]
        with patch.object(busy, "AnsibleModule", return_value=module), patch.object(busy, "Api", return_value=api), \
                patch.object(busy, "connection", return_value={"host": "controller", "login": "token"}):
            for expected in ([], [103], [97, 98, 99]):
                busy.main()
                module.exit_json.assert_called_with(changed=False, checked=True, busy=expected, why="")

    def test_total_swis_failure_report_severity(self):
        host = {"host": "SRV-APP01", "result": {"restart": True, "scheduled": {"id": "1"}}}
        for state, status in (("error", "warning"), ("off", "ok"), ("nocred", "ok")):
            opts = {"swis_state": state, "swis_note": w.win_restart_swis_note(state, "HTTP 401")}
            doc = w.win_restart_doc(w.win_restart_model([host], opts), WHEN)
            self.assertEqual(doc["status"], status)
            self.assertIn(opts["swis_note"], doc["sections"][1]["lines"])
        self.assertEqual(w.win_restart_doc(w.win_restart_model([{"host": "SRV-APP01", "unreached": "timeout"}],
                                                               {"swis_state": "error"}), WHEN)["status"], "critical")

    def test_items_and_rows(self):
        hosts = [{"host": "a", "ip": "192.0.2.10", "result": {"restart": True, "scheduled": {"id": "1"}}},
                 {"host": "b", "result": {"restart": True, "scheduled": {"id": "2"}}},
                 {"host": "d", "result": {"restart": False}},
                 {"host": "e", "result": {"error": "Access is denied"}}]
        items = w.win_restart_swis_items(hosts, "schedule", False)
        self.assertEqual([(i["host"], i["ip"]) for i in items], [("a", "192.0.2.10"), ("b", "")])
        mp = w.win_restart_swis_map(hosts, {"a": {"text": "SolarWinds: x", "level": "ok"}})
        self.assertEqual(mp["d"]["text"], "SolarWinds: not unmanaged (skipped: no reboot pending)")
        self.assertNotIn("e", mp)
        self.assertEqual(w.win_restart_swis_map(hosts, {}, "nocred"), {})
        self.assertIn("no credential - node stays managed", w.win_restart_swis_note("nocred"))
        m = w.win_restart_model([dict(hosts[1], result=res("b", ["Windows Update"], scheduled={"id": "2"}),
                                      swis={"text": "SolarWinds: node not found (x) - not unmanaged", "level": "warning"})],
                                {"mode": "schedule", "swis_window": "22:25-22:40 EST"})
        d = w.win_restart_doc(m, WHEN)
        self.assertEqual(d["sections"][0]["columns"][-1], "SolarWinds")
        self.assertEqual(d["sections"][0]["hidden_columns"], ["Up since", "Restart at (server time)"])
        self.assertEqual(d["sections"][0]["kinds"][0], "nw")
        self.assertEqual(d["status"], "warning")
        self.assertIn("22:25-22:40 EST", " ".join(d["sections"][1]["lines"]))
        m = w.win_restart_model([{"host": "a", "result": {"ours": [], "removed": [{"name": "t", "path": "\\x\\"}]},
                                  "swis": {"text": "SolarWinds: the unmanage window x stays and ends by itself", "level": "info"}}], {"mode": "cancel"})
        self.assertEqual(w.win_restart_cancel_doc(m)["sections"][0]["rows"][0][-1], "the unmanage window x stays and ends by itself")


class Catalog(unittest.TestCase):
    def test_generated(self):
        for s in ("gen_job_catalog.py", "gen_template_vars.py", "gen_variable_reference.py"):
            r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", s), "--check"], capture_output=True, text=True, cwd=REPO)
            self.assertEqual(r.returncode, 0, s + r.stdout + r.stderr)

    def test_entries(self):
        import yaml
        with open(os.path.join(REPO, "aap", "job_templates.yml")) as fh:
            cat = yaml.safe_load(fh)
        jt = dict((e["name"], e) for e in cat["job_templates"])
        for name, pb in (("Windows - schedule a restart", "win_restart_schedule.yml"), ("Windows - cancel a scheduled restart", "win_restart_cancel.yml")):
            e = jt[name]
            self.assertEqual(e["playbook"], "playbooks/" + pb)
            self.assertEqual((e["credentials"], e["ee"], e["area"]), (["machine_windows"], "windows", "Windows"))
            # the schedule job's unmanage credential only (never the read-only solarwinds kind); cancel calls nothing in SolarWinds
            self.assertEqual(e["optional_credentials"], ["solarwinds_manage", "aap"] if "schedule" in pb else [])
            self.assertTrue(e["ask_limit"] and e["ask_job_type"] and e["portal"])
            self.assertTrue(all(e["description"][k] for k in ("does", "targets", "produces", "supports")))
            self.assertTrue(e["description"]["supports"].startswith("Run constraint="))
        qs = dict((q["variable"], q) for q in jt["Windows - schedule a restart"]["survey"])
        self.assertEqual(list(qs), ["win_restart_time", "win_restart_only_if_pending", "win_restart_reason", "win_restart_change"])
        self.assertTrue(qs["win_restart_time"]["required"] and qs["win_restart_reason"]["required"])
        self.assertEqual(qs["win_restart_only_if_pending"]["choices"], ["no", "yes"])
        # the portal reads that survey as a form (text, a choice), its Hosts box from the Limit
        import site_portal_filters as spf
        v, skipped = spf.survey_vars({"spec": jt["Windows - schedule a restart"]["survey"]})
        self.assertEqual(skipped, [])
        self.assertEqual(sorted(v), sorted(qs))


@unittest.skipUnless(shutil.which("ansible-playbook"), "no ansible-playbook")
class Playbook(unittest.TestCase):
    def test_syntax(self):
        bins = [shutil.which("ansible-playbook")] + [b for b in [os.environ.get("ANSIBLE_PLAYBOOK")] if b and os.path.exists(b)]
        for b in bins:
            for pb in ("win_restart_schedule.yml", "win_restart_cancel.yml"):
                tmp = tempfile.mkdtemp()
                try:
                    r = subprocess.run([b, "--syntax-check", "-i", "localhost,", os.path.join(REPO, "playbooks", pb)], capture_output=True,
                                       text=True, timeout=300, cwd=REPO, env=dict(os.environ, ANSIBLE_LOCAL_TEMP=tmp))
                    self.assertEqual(r.returncode, 0, b + pb + r.stdout + r.stderr)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)

    def run_local(self, pb, extra, check=True, binary=None, env=None):
        """The playbook on two stand-in servers: this machine (connection local, taken as Windows), the script under pwsh."""
        tmp = tempfile.mkdtemp()
        try:
            inv = os.path.join(tmp, "inv")
            with open(inv, "w") as fh:
                fh.write("srv-app01 ansible_connection=local ansible_python_interpreter=%s\nsrv-app02 ansible_connection=local ansible_python_interpreter=%s\n"
                         % (sys.executable, sys.executable))
            ev = dict({"site_windows_connections": ["local"], "_wr_script_executable": "pwsh"}, **extra)
            return subprocess.run([binary or "ansible-playbook", "-i", inv, "-l", "srv-app0*", os.path.join(REPO, "playbooks", pb)]
                                  + (["--check"] if check else []) + ["-e", json.dumps(ev)],
                                  capture_output=True, text=True, timeout=600, cwd=REPO, env=dict(dict((k, v) for k, v in os.environ.items() if not k.startswith("SOLARWINDS")),
                                                                     ANSIBLE_LOCAL_TEMP=tmp, TZ="Asia/Kolkata", ANSIBLE_FORKS="1", **(env or {})))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
    def test_plan_end_to_end(self):
        bins = [None] + [b for b in [os.environ.get("ANSIBLE_PLAYBOOK")] if b and os.path.exists(b)]
        for b in bins:
            r = self.run_local("win_restart_schedule.yml", {"win_restart_time": "23:59", "win_restart_reason": "Monthly patches",
                                                           "win_restart_time_zone": "UTC", "win_restart_min_lead_minutes": 0}, binary=b)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn("srv-app01: WOULD RESTART - at ", r.stdout)
            self.assertIn("srv-app02: WOULD RESTART - at ", r.stdout)
            self.assertIn("UTC+05:30 (Asia/Kolkata)", r.stdout)           # the stand-in server's own zone
            self.assertIn("SolarWinds: no credential - node stays managed", r.stdout)
            # a SolarWinds that cannot be reached: said, the plan goes on, never a failure
            r = self.run_local("win_restart_schedule.yml", {"win_restart_time": "23:59", "win_restart_reason": "x", "win_restart_time_zone": "UTC",
                                                           "win_restart_min_lead_minutes": 0, "win_restart_solarwinds_timeout": 2}, binary=b,
                               env={"ANSIBLE_VERBOSITY": "1", "SOLARWINDS_MANAGE_URL": "https://192.0.2.1", "SOLARWINDS_MANAGE_USERNAME": "u", "SOLARWINDS_MANAGE_PASSWORD": "p"})
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn("SolarWinds: SolarWinds could not be read", r.stdout)
            self.assertIn('"swis_state": "error"', r.stdout)
            # only the read-only credential's variables (a report's): never taken for a write - no credential
            r = self.run_local("win_restart_schedule.yml", {"win_restart_time": "23:59", "win_restart_reason": "x", "win_restart_time_zone": "UTC",
                                                           "win_restart_min_lead_minutes": 0}, binary=b,
                               env={"SOLARWINDS_URL": "https://192.0.2.1", "SOLARWINDS_USERNAME": "u", "SOLARWINDS_PASSWORD": "p"})
            self.assertIn("SolarWinds: no credential - node stays managed", r.stdout)
            # win_restart_solarwinds: false - never asked
            r = self.run_local("win_restart_schedule.yml", {"win_restart_time": "23:59", "win_restart_reason": "x", "win_restart_time_zone": "UTC",
                                                           "win_restart_min_lead_minutes": 0, "win_restart_solarwinds": False}, binary=b)
            self.assertIn("SolarWinds: off (win_restart_solarwinds: false)", r.stdout)
            # only if pending, nothing pending on this machine: skipped, the job green
            r = self.run_local("win_restart_schedule.yml", {"win_restart_time": "23:59", "win_restart_reason": "x", "win_restart_time_zone": "UTC",
                                                           "win_restart_min_lead_minutes": 0, "win_restart_only_if_pending": "yes"}, binary=b)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn("srv-app01: SKIPPED - pending: no", r.stdout)
            r = self.run_local("win_restart_cancel.yml", {}, binary=b)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn("srv-app01: NOTHING TO CANCEL", r.stdout)

    @unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
    def test_excluded_server_left_out(self):
        # srv-app01 in site_exclude_hosts, A,B picked: only srv-app02 - by target and by Limit; never the excluded one alone
        bins = [None] + [x for x in [os.environ.get("ANSIBLE_PLAYBOOK")] if x and os.path.exists(x)]
        for binary in bins:
            for pb in ("win_restart_cancel.yml", "win_restart_schedule.yml"):
                for extra in ({"target": "srv-app01:srv-app02"}, {}):
                    r = self.run_local(pb, dict(extra, site_exclude_hosts=["srv-app01"], win_restart_time="23:59", win_restart_reason="x",
                                                win_restart_time_zone="UTC", win_restart_min_lead_minutes=0), binary=binary)
                    self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
                    self.assertIn("The servers (1): srv-app02", r.stdout)
                    self.assertNotIn("srv-app01: ", r.stdout)
        # exclusions set, nothing picked: refused (it would be every server left)
        tmp = tempfile.mkdtemp()
        try:
            inv = os.path.join(tmp, "inv")
            with open(inv, "w") as fh:
                fh.write("srv-app01 ansible_connection=local\nsrv-app02 ansible_connection=local\n")
            for pb in ("win_restart_cancel.yml", "win_restart_schedule.yml"):
                r = subprocess.run(["ansible-playbook", "-i", inv, os.path.join(REPO, "playbooks", pb), "--check", "-e",
                                    json.dumps({"target": "", "site_exclude_hosts": ["srv-app01"], "site_windows_connections": ["local"],
                                                "win_restart_time": "23:59", "win_restart_reason": "x", "win_restart_time_zone": "UTC"})],
                                   capture_output=True, text=True, timeout=300, cwd=REPO, env=dict(os.environ, ANSIBLE_LOCAL_TEMP=tmp))
                self.assertNotEqual(r.returncode, 0, pb)
                self.assertIn("no servers picked", r.stdout, pb)
                self.assertNotIn("Reach the host", r.stdout, pb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_limit_refused(self):
        # no Limit: refused before any server is touched (localhost stands in for a server; nothing connects)
        tmp = tempfile.mkdtemp()
        try:
            inv = os.path.join(tmp, "inv")
            with open(inv, "w") as fh:
                fh.write("srv-app01 ansible_connection=winrm ansible_host=192.0.2.10\n")
            r = subprocess.run(["ansible-playbook", "-i", inv, os.path.join(REPO, "playbooks", "win_restart_schedule.yml"), "--check",
                                "-e", json.dumps({"win_restart_time": "22:30", "win_restart_reason": "x", "win_restart_time_zone": "UTC"})],
                               capture_output=True, text=True, timeout=300, cwd=REPO, env=dict(os.environ, ANSIBLE_LOCAL_TEMP=tmp))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Nothing was read or changed", r.stdout)
            self.assertIn("no servers picked", r.stdout)
            self.assertNotIn("Reach the host", r.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
