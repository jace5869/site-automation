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
        self.assertEqual(w.win_restart_hosts("web:&prod", ""), "web:&prod")
        self.assertEqual(w.win_restart_selector("web[01:03]", ""), ["web[01:03]"])
        self.assertIn("at most 50", self.ok(hosts=51)[0])

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
        self.assertIn("/inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'", t)   # the script SYSTEM runs: admins only
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
function icacls.exe { $global:LASTEXITCODE = 0 }
function Step($cfg) {
    $env:SITE_CHECK_CONFIG = ($cfg | ConvertTo-Json -Compress)
    $line = (. $global:PS | Select-Object -Last 1)
    $o = ConvertFrom-Json ($line -replace '^###SITE-JSON### ', '')
    $files = @(Get-ChildItem -Path $cfg.dir -File -ErrorAction SilentlyContinue | ForEach-Object { $_.Name } | Sort-Object)
    $t = $global:Store[$cfg.task_path + $cfg.task_name]
    $args_ = ''
    if ($null -ne $t) { $args_ = [string]$t.Actions[0].Arguments }
    Write-Output ('STEP ' + (@{ out = $o; files = $files; task = $args_; author = [string]$t.Author } | ConvertTo-Json -Compress -Depth 6))
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
            d = os.path.join(tmp, "restart")
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
            r = pwsh("\n".join(code), {"SystemRoot": os.path.join(tmp, "root")})
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

    def test_solarwinds_record_kept(self):
        rec = {"node_id": 412, "from": "2026-11-25T03:25:00Z", "until": "2026-11-25T03:40:00Z"}
        a, b = self.run_steps([{"action": "schedule", "only_if_pending": False, "solarwinds": rec},
                               {"action": "schedule", "only_if_pending": False, "later": True}])
        got = b["out"]["replaced"][0]["solarwinds"]
        self.assertEqual(int(got["node_id"]), 412)
        self.assertEqual(rs.parse(got["from"]), rs.parse(rec["from"]))
        self.assertEqual(w.win_restart_swis_items([{"host": "h", "result": b["out"]}], {}, {}, "schedule")[0]["earlier"]["node_id"], 412)

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
$ok = '-NoProfile -File "' + (Join-Path $d 'restart-task-1.ps1') + '"'
function T($author, $desc, $args_) { Test-SiteOurTask -Task ([pscustomobject]@{ Author = $author; Description = $desc; Actions = @([pscustomobject]@{ Arguments = $args_ }) }) -Dir $d }
'MARKS ' + (@((T 'site-automation' 'x (site-automation, AAP job 1)' $ok), (T 'Other' 'x (site-automation)' $ok), (T 'site-automation' 'x' $ok),
  (T 'site-automation' 'x (site-automation)' '-File "/other/restart-task-1.ps1"')) -join ',')
"""
        r = pwsh(code, {"SITE_CHECK_CONFIG": json.dumps({"action": "none", "task_name": "x", "task_path": "\\x\\", "dir": "/nonexistent",
                                                         "task_script": read(TASK)})})
        self.assertIn("MARKS True,False,False,False", r.stdout, r.stdout + r.stderr)


sys.path.insert(0, os.path.join(REPO, "plugins", "module_utils"))
import site_win_restart_swis as rs  # noqa: E402


class FakeSwis(object):
    """Orion.Nodes as SWIS answers it: SELECTs by name, DNS, IP or NodeID; the two verbs recorded (or failing)."""

    def __init__(self, nodes, fail=None):
        self.nodes, self.calls, self.fail = nodes, [], fail

    def query(self, swql, params=None):
        p = dict((k, str(v).lower()) for k, v in (params or {}).items())
        if "NodeID = @id" in swql:
            return [n for n in self.nodes if int(n["NodeID"]) == int(params["id"])]
        out = []
        for n in self.nodes:
            cap, dns, ip = str(n["Caption"]).lower(), str(n.get("DNS") or "").lower(), str(n.get("IPAddress") or "")
            if (cap in (p.get("short"), p.get("full")) or dns in (p.get("short"), p.get("full")) or ip == p.get("ip")
                    or cap.startswith(p.get("short", "#") + ".") or dns.startswith(p.get("short", "#") + ".")):
                out.append(n)
        return out

    def invoke(self, verb, args):
        if self.fail:
            raise rs.sw.SwisError(self.fail, "auth")
        self.calls.append([verb] + list(args))
        nid = int(str(args[0]).split(":")[1])
        n = next(x for x in self.nodes if int(x["NodeID"]) == nid)
        if verb == "Unmanage":
            n.update(UnManageFrom=args[1][:19], UnManageUntil=args[2][:19], UnManaged=False)
        else:
            n.update(UnManaged=False, UnManageFrom="", UnManageUntil="")
        return True


def orion():
    return [{"NodeID": 412, "Caption": "SRV-APP01", "DNS": "srv-app01.corp.example.mil", "IPAddress": "192.0.2.10"},
            {"NodeID": 418, "Caption": "SRV-DB01", "DNS": "srv-db01.corp.example.mil", "IPAddress": "192.0.2.11"},
            {"NodeID": 501, "Caption": "srv-web02", "DNS": "", "IPAddress": "192.0.2.20"},
            {"NodeID": 502, "Caption": "SRV-WEB02", "DNS": "", "IPAddress": "192.0.2.21"}]


NOW = at(2026, 11, 24, 15, 0)
WIN = rs.window("2026-11-25T03:30:00Z", 5, 10)


class SolarWinds(unittest.TestCase):
    def test_window_in_utc(self):
        self.assertEqual(WIN, {"from": "2026-11-25T03:25:00Z", "until": "2026-11-25T03:40:00Z"})
        self.assertEqual(rs.window("2026-11-25T03:30:00Z"), WIN)                    # the defaults: 5 before, 10 after
        import zoneinfo
        self.assertEqual(rs.shown(WIN, zoneinfo.ZoneInfo("America/New_York")), "22:25-22:40 EST")
        self.assertEqual(rs.shown(rs.window("2026-11-25T04:55:00Z", 5, 10), zoneinfo.ZoneInfo("America/New_York")), "23:50-2026-11-25 00:05 EST")

    def test_orion_times_utc_or_local(self):
        import zoneinfo
        ber = zoneinfo.ZoneInfo("Europe/Berlin")
        win = rs.window("2026-11-24T21:30:00Z")                      # 22:30 CET: 22:25-22:40 CET = 21:25-21:40 UTC
        # naive, as UTC (fractions too)
        self.assertEqual(rs.same({"from": "2026-11-24T21:25:00.0000000", "until": "2026-11-24T21:40:00"}, win, ber), "utc")
        # naive, as the site's local time
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00", "until": "2026-11-24T22:40:00"}, win, ber), "local")
        # with a zone: exactly that
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00+01:00", "until": "2026-11-24T21:40:00Z"}, win, ber), "utc")
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00Z", "until": "2026-11-24T22:40:00Z"}, win, ber), "")    # Z: never local
        # within 90 s; neither reading: not ours
        self.assertEqual(rs.same({"from": "2026-11-24T21:26:00", "until": "2026-11-24T21:41:00"}, win, ber), "utc")
        self.assertEqual(rs.same({"from": "2026-11-24T20:25:00", "until": "2026-11-24T23:40:00"}, win, ber), "")
        self.assertEqual(rs.same({"from": "2026-11-24T22:25:00", "until": "2026-11-24T22:40:00"}, win), "")         # no site zone: UTC only
        # in a run: our earlier window read back as local time - ours, replaced, and said which reading matched
        nodes = orion()
        nodes[0].update(UnManageFrom="2026-11-24T22:25:00", UnManageUntil="2026-11-24T22:40:00")
        c = FakeSwis(nodes)
        new = rs.window("2026-11-24T22:30:00Z")
        res = rs.run(c, [{"host": "a", "op": "schedule", "node_id": 412, "window": new, "earlier": dict(win, node_id=412)}],
                     at(2026, 11, 24, 21, 0), False, ber)
        self.assertEqual(c.calls[0], ["Remanage", "N:412"])
        self.assertEqual(res["a"]["times"], "local")
        # a person's window, neither reading: left, a warning
        nodes[0].update(UnManageFrom="2026-11-24T19:00:00", UnManageUntil="2026-11-25T06:00:00")
        c = FakeSwis(nodes)
        res = rs.run(c, [{"host": "a", "op": "schedule", "node_id": 412, "window": new, "earlier": dict(win, node_id=412)}],
                     at(2026, 11, 24, 21, 0), False, ber)
        self.assertEqual((c.calls, res["a"]["level"]), ([], "warning"))
        self.assertIn("unmanaged by someone else", res["a"]["text"])
        # the report's note
        m = w.win_restart_model([], {"mode": "schedule", "swis_window": "x", "swis_times": ["local"]})
        self.assertIn("SolarWinds returns times as local time", " ".join(w.win_restart_doc(m, WHEN)["sections"][1]["lines"]))

    def test_find_exact_or_nothing(self):
        f = rs.find(FakeSwis(orion()), [{"host": "srv-app01.corp.example.mil", "ip": "192.0.2.10"}, {"host": "srv-web02", "ip": ""},
                                        {"host": "srv-gone.corp.example.mil", "ip": ""}])
        self.assertEqual(f["srv-app01.corp.example.mil"]["node"]["node_id"], 412)
        self.assertIsNone(f["srv-web02"]["node"])
        self.assertIn("2 SolarWinds nodes", f["srv-web02"]["why"])
        self.assertIsNone(f["srv-gone.corp.example.mil"]["node"])

    def test_unmanage_call(self):
        c = FakeSwis(orion())
        res = rs.run(c, [{"host": "a", "op": "schedule", "node_id": 412, "window": WIN, "earlier": {}}], NOW, False)
        self.assertEqual(c.calls, [["Unmanage", "N:412", "2026-11-25T03:25:00Z", "2026-11-25T03:40:00Z", False]])
        self.assertTrue(res["a"]["done"])
        self.assertIn("node SRV-APP01 (N:412) unmanaged", res["a"]["text"])
        # Check: the same words, nothing invoked
        c = FakeSwis(orion())
        res = rs.run(c, [{"host": "a", "op": "schedule", "node_id": 412, "window": WIN, "earlier": {}}], NOW, True)
        self.assertEqual(c.calls, [])
        self.assertIn("would be unmanaged", res["a"]["text"])
        # not found: a warning, never a call
        res = rs.run(FakeSwis(orion()), [{"host": "b", "op": "schedule", "node_id": None, "why": "2 SolarWinds nodes are named srv-web02",
                                         "window": WIN}], NOW, False)
        self.assertEqual((res["b"]["level"], res["b"]["done"]), ("warning", False))
        self.assertIn("node not found (2 SolarWinds nodes are named srv-web02) - not unmanaged", res["b"]["text"])

    def test_ours_replaced_a_persons_left(self):
        nodes = orion()
        nodes[0].update(UnManageFrom="2026-11-24T22:55:00", UnManageUntil="2026-11-24T23:30:00")      # ours, from an earlier run
        nodes[1].update(UnManageFrom="2026-11-24T20:00:00", UnManageUntil="2026-11-25T06:00:00")      # a person's
        c = FakeSwis(nodes)
        ours = {"node_id": 412, "from": "2026-11-24T22:55:00Z", "until": "2026-11-24T23:30:00Z"}
        res = rs.run(c, [{"host": "a", "op": "schedule", "node_id": 412, "window": WIN, "earlier": ours},
                         {"host": "b", "op": "schedule", "node_id": 418, "window": WIN, "earlier": ours}], NOW, False)
        self.assertEqual(c.calls, [["Remanage", "N:412"], ["Unmanage", "N:412", WIN["from"], WIN["until"], False]])
        self.assertIn("our earlier window", res["a"]["text"])
        self.assertEqual(res["b"]["level"], "warning")
        self.assertIn("unmanaged by someone else", res["b"]["text"])

    def test_remanage_only_ours(self):
        nodes = orion()
        nodes[0].update(UnManageFrom="2026-11-25T03:25:00", UnManageUntil="2026-11-25T03:40:00")
        nodes[1].update(UnManageFrom="2026-11-25T01:00:00", UnManageUntil="2026-11-25T09:00:00")
        c = FakeSwis(nodes)
        res = rs.run(c, [{"host": "a", "op": "remove", "earlier": dict(WIN, node_id=412)},
                         {"host": "b", "op": "remove", "earlier": dict(WIN, node_id=418)},
                         {"host": "c", "op": "remove", "earlier": dict(WIN, node_id=501)}], NOW, False)
        self.assertEqual(c.calls, [["Remanage", "N:412"]])
        self.assertIn("managed again", res["a"]["text"])
        self.assertIn("not ours", res["b"]["text"])
        self.assertIn("is managed - nothing to do", res["c"]["text"])

    def test_a_failed_call_is_a_warning(self):
        res = rs.run(FakeSwis(orion(), fail="SolarWinds refused it (HTTP 403): the account may not manage nodes"),
                     [{"host": "a", "op": "schedule", "node_id": 412, "window": WIN, "earlier": {}}], NOW, False)
        self.assertEqual(res["a"]["level"], "warning")
        self.assertIn("unmanage failed (SolarWinds refused it (HTTP 403)", res["a"]["text"])
        self.assertIn("the restart stays as it is", res["a"]["text"])

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
        m.invoke("Remanage", ["N:412"])
        self.assertEqual(sent[0], ("https://orion.example.mil:17774/SolarWinds/InformationService/v3/Json/Invoke/Orion.Nodes/Unmanage",
                                   ["N:412", "2026-11-25T03:25:00Z", "2026-11-25T03:40:00Z", False], "POST"))
        self.assertTrue(sent[1][0].endswith("/Invoke/Orion.Nodes/Remanage"))
        with self.assertRaises(rs.sw.SwisError):
            m.invoke("Delete", ["N:412"])
        with self.assertRaises(rs.sw.SwisError):            # the read-only client's SELECT-only rule still holds
            m.query("DELETE FROM Orion.Nodes")

    def test_items_and_rows(self):
        found = {"a": {"node": {"node_id": 412}, "why": ""}, "b": {"node": None, "why": "no node"}}
        rec = dict(WIN, node_id=7)
        hosts = [{"host": "a", "result": {"restart": True, "scheduled": {"id": "1"}, "replaced": [{"solarwinds": rec}]}},
                 {"host": "b", "result": {"restart": True, "scheduled": {"id": "2"}}},
                 {"host": "c", "result": {"restart": False, "removed": [{"solarwinds": rec}]}},
                 {"host": "d", "result": {"restart": False}},
                 {"host": "e", "result": {"error": "Access is denied"}}]
        items = w.win_restart_swis_items(hosts, found, WIN, "schedule", False)
        self.assertEqual([(i["host"], i["op"]) for i in items], [("a", "schedule"), ("b", "schedule"), ("c", "remove")])
        self.assertEqual(items[0]["earlier"], {"node_id": 7, "from": WIN["from"], "until": WIN["until"]})
        self.assertEqual(w.win_restart_swis_record(found["a"], WIN), {"node_id": 412, "from": WIN["from"], "until": WIN["until"]})
        self.assertEqual(w.win_restart_swis_record(found["b"], WIN), {})
        mp = w.win_restart_swis_map(hosts, {"a": {"text": "SolarWinds: x", "level": "ok"}})
        self.assertEqual(mp["d"]["text"], "SolarWinds: not unmanaged (skipped: no reboot pending)")
        self.assertNotIn("e", mp)
        self.assertEqual(w.win_restart_swis_map(hosts, {}, "nocred"), {})
        self.assertIn("no credential - node stays managed", w.win_restart_swis_note("nocred"))
        # the report: one more column; a SolarWinds warning makes the report a warning, never a failure
        m = w.win_restart_model([dict(hosts[1], result=res("b", ["Windows Update"], scheduled={"id": "2"}),
                                      swis={"text": "SolarWinds: node not found (x) - not unmanaged", "level": "warning"})],
                                {"mode": "schedule", "swis_window": "22:25-22:40 EST"})
        d = w.win_restart_doc(m, WHEN)
        self.assertEqual(d["sections"][0]["columns"][-1], "SolarWinds")
        self.assertEqual(d["sections"][0]["hidden_columns"], ["Up since", "Restart at (server time)"])
        self.assertEqual(d["sections"][0]["kinds"][0], "nw")
        self.assertEqual(d["status"], "warning")
        self.assertIn("22:25-22:40 EST", " ".join(d["sections"][1]["lines"]))
        # cancel: the column too
        m = w.win_restart_model([{"host": "a", "result": {"ours": [], "removed": [{"name": "t", "path": "\\x\\"}]},
                                  "swis": {"text": "SolarWinds: node A (N:1) managed again", "level": "ok"}}], {"mode": "cancel"})
        self.assertEqual(w.win_restart_cancel_doc(m)["sections"][0]["rows"][0][-1], "node A (N:1) managed again")


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
            self.assertEqual(e["optional_credentials"], ["solarwinds_manage"])       # never the read-only solarwinds kind
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
                                  capture_output=True, text=True, timeout=600, cwd=REPO, env=dict(dict((k, v) for k, v in os.environ.items() if not k.startswith("SOLARWINDS_")),
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
                               env={"SOLARWINDS_URL": "https://192.0.2.1", "SOLARWINDS_USERNAME": "u", "SOLARWINDS_PASSWORD": "p"})
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr)
            self.assertIn("SolarWinds: SolarWinds could not be read", r.stdout)
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
                for extra in ({"target": "srv-app01,srv-app02"}, {}):
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
