#!/usr/bin/env python3
"""1.74.0: Patch hosts' report (plugins/filter/patch_report_filters.py) - every host's state from what its steps left,
the problem hosts worst first with their reason, step, what to do and snapshot; a dry run titled as one.
    python -m unittest tests.test_patch_report"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import patch_report_filters as pr  # noqa: E402
import site_filters as sf  # noqa: E402

TOOK = 'took "patching" of VM web01-vm'
DONE = {"updates": 4, "rebooted": True, "reboot": "", "warn_services": []}


def hosts():
    return {
        "web01.example.mil": {"_patch_done": DONE, "_patch_snapshot": TOOK, "patch_started": True},
        "web02.example.mil": {"_patch_done": dict(DONE, rebooted=False, reboot="needed - automatic_restarts is false",
                                                  warn_services=["myapp.service"]), "patch_started": True},
        "db01.example.mil": {"patch_up_to_date": True, "patch_reboot_pending": True},
        "db02.example.mil": {"patch_up_to_date": True, "patch_reboot_pending": False},
        "aap01.example.mil": {"_patch_ended_on_purpose": "never-patch group"},
        "down01.example.mil": {"site_host_unreached": "Failed to connect to the host via ssh: ssh: connect to host 192.0.2.9 port 22: No route to host"},
        "nr01.example.mil": {"patch_not_ready": "only 200 MB free in /var (need 1024 MB)"},
        "bad01.example.mil": {"patch_started": True, "_patch_snapshot": TOOK,
                              "_patch_could_not": {"reason": "FAILED during update", "step": "the update",
                                                   "detail": "Error: Transaction test error:\n  file /usr/lib/x conflicts between attempted installs\n"}},
        "lost01.example.mil": {"patch_started": True, "_patch_snapshot": TOOK,
                               "_patch_could_not": {"reason": "LOST mid-run", "step": "the reboot", "detail": "LOST at the reboot: Failed to connect"}},
        "svc01.example.mil": {"patch_started": True, "_patch_snapshot": 'reused "patching" from 2026-11-18 05:00 UTC of VM svc01',
                              "_patch_could_not": {"reason": "PATCHED - SERVICES DOWN", "step": "the services after the update",
                                                   "detail": "After patching svc01, these services are not running: httpd.service.",
                                                   "services": ["httpd.service"]}},
        "dup01.example.mil": {"patch_could_not": {"reason": "TWO VMs", "detail": "dup01 matches more than one VM (a, b): set vmware_vm_name"}},
    }


class HostState(unittest.TestCase):
    def test_each_reason(self):
        h = hosts()
        st = dict((k, pr.patch_host_state(v)) for k, v in h.items())
        self.assertEqual(st["web01.example.mil"]["state"], "patched")
        self.assertEqual(st["db01.example.mil"]["reboot"], "REBOOT PENDING")
        self.assertEqual(st["aap01.example.mil"]["state"], "skipped")
        self.assertEqual((st["down01.example.mil"]["state"], st["down01.example.mil"]["reason"]), ("could_not", "NOT REACHED"))
        self.assertEqual(st["nr01.example.mil"]["reason"], "NOT READY")
        self.assertEqual(st["bad01.example.mil"]["reason"], "FAILED during update")
        self.assertIn("conflicts between attempted installs", st["bad01.example.mil"]["detail"])     # the error's last lines
        self.assertEqual((st["svc01.example.mil"]["state"], st["svc01.example.mil"]["warn_services"]), ("services_down", ["httpd.service"]))
        self.assertEqual(st["dup01.example.mil"]["reason"], "TWO VMs")
        self.assertTrue(all(st[k]["todo"] for k in st if st[k]["state"] in pr.NEEDS_LOOK))        # always what to do
        self.assertEqual(pr.patch_host_state({"patch_unsupported": "Ubuntu 24.04, apt"})["reason"], "NO DNF OR YUM")
        self.assertEqual(pr.patch_host_state({"patch_not_started": "the rollout stopped"})["state"], "not_started")
        self.assertEqual(pr.patch_host_state({"patch_started": True})["reason"], "LOST mid-run")   # started, nothing more known
        self.assertEqual(pr.patch_host_state({})["state"], "skipped")

    def test_short_detail(self):
        d = pr.patch_host_state({"_patch_could_not": {"reason": "FAILED during update", "step": "the update",
                                                      "detail": "x" * 400}})["detail"]
        self.assertLessEqual(len(d), 160)
        self.assertEqual(pr._short("Task failed: Module failed: boom\nShared connection to h closed."), "boom")
        self.assertEqual(pr._short("LOST at the reboot: Failed to connect"), "Failed to connect")


class Report(unittest.TestCase):
    def test_the_report(self):
        r = pr.patch_report(hosts(), {"serial": 2, "snapshot": True})
        self.assertEqual(r["status"], "critical")
        self.assertTrue(r["title"].startswith("Patch hosts: 2 hosts patched, 5 could not be patched, 1 with services down"))
        self.assertEqual([(s["label"], s["value"]) for s in r["summary"]],
                         [("Patched", 2), ("Up to date", 2), ("Could not patch", 5), ("Skipped on purpose", 1)])
        self.assertEqual(r["summary"][2]["note"], "+ 1 patched, services down")
        titles = [s["title"] for s in r["sections"]]
        self.assertEqual(titles, ["Start here", "Could not patch or needs a look", "Patched", "Up to date", "Skipped on purpose", "Notes"])
        look = r["sections"][1]
        self.assertEqual(look["columns"], ["Host", "Reason", "Step", "What to do", "Snapshot"])
        # worst first: lost, then services down, then the update failure, ... not reached last
        self.assertEqual([x[0] for x in look["rows"]], ["lost01.example.mil", "svc01.example.mil", "bad01.example.mil",
                                                       "dup01.example.mil", "nr01.example.mil", "down01.example.mil"])
        self.assertEqual(look["rows"][0][4], "snapshot patching kept - roll back from it if needed")
        self.assertEqual(look["rows"][1][4], "snapshot patching kept - roll back from it if needed")       # reused: kept too
        self.assertEqual(look["rows"][-1][4], "none")
        self.assertTrue(look["rows"][1][1].startswith("PATCHED - SERVICES DOWN"))
        self.assertEqual(len(r["sections"][0]["cards"]), 3)
        patched = r["sections"][2]
        self.assertEqual(patched["rows"][1], ["web02.example.mil", "4", "no - needed - automatic_restarts is false",
                                              "WARN stopped: myapp.service", "none"])
        self.assertEqual(patched["row_status"], ["", "warning"])
        self.assertEqual(r["sections"][3]["rows"], [["db01.example.mil", "REBOOT PENDING"], ["db02.example.mil", "none pending"]])
        self.assertEqual(r["counts"], {"critical": 6, "warning": 1, "low": 0})                     # the index's counts
        self.assertEqual(sorted(r["needs_look"]), sorted(["lost01.example.mil", "svc01.example.mil", "bad01.example.mil",
                                                          "dup01.example.mil", "nr01.example.mil", "down01.example.mil"]))
        self.assertIn("The job ends failed because 6 hosts need a look (patch_fail_on_could_not).", r["sections"][-1]["lines"])

    def test_a_dry_run(self):
        h = hosts()
        h["web01.example.mil"]["_patch_snapshot"] = 'would take "patching" of VM web01-vm'
        r = pr.patch_report(h, {"dry": True})
        self.assertTrue(r["title"].startswith("DRY RUN - Patch hosts: 2 hosts would be patched, 5 could not be patched"))
        self.assertTrue(r["subtitle"].startswith("Check mode: nothing was installed"))
        self.assertEqual(r["summary"][0]["label"], "Would patch")
        self.assertEqual([s["title"] for s in r["sections"]][2], "Would patch")
        self.assertEqual(r["sections"][2]["rows"][0][2], "no (dry run)")
        self.assertEqual(r["sections"][2]["rows"][0][4], 'would take "patching" of VM web01-vm')
        look = r["sections"][1]
        self.assertEqual([x[4] for x in look["rows"]][0], TOOK)                                  # a dry run keeps no snapshot
        self.assertEqual([c[2] for c in r["sections"][2]["cell_status"]], ["", ""])                # nor flags a reboot
        self.assertIn("A dry run never fails the job, so the approval can follow it: the hosts above would not be patched.", r["sections"][-1]["lines"])

    def test_all_well_and_the_stop(self):
        ok = pr.patch_report({"web01": {"_patch_done": DONE}}, {})
        self.assertEqual((ok["status"], ok["needs_look"]), ("ok", []))
        self.assertNotIn("Could not patch or needs a look", [s["title"] for s in ok["sections"]])
        self.assertEqual(ok["counts"], {"critical": 0, "warning": 0, "low": 0})
        stop = pr.patch_report({"bad": {"_patch_could_not": {"reason": "FAILED during update", "step": "the update", "detail": "e"}},
                                "later": {"patch_not_started": "Not patched: bad failed or was lost in an earlier batch"}},
                               {"continue_on_failure": False, "stopped": "Not patched: bad failed or was lost in an earlier batch"})
        self.assertEqual(stop["summary"][-1]["label"], "Not started")
        self.assertEqual(stop["sections"][1]["row_status"], ["critical", "warning"])
        self.assertIn("Not patched: bad failed or was lost in an earlier batch", stop["sections"][-1]["lines"])
        self.assertIn("patch_continue_on_failure is false: the rollout stops at the first host that fails or is lost.", stop["sections"][-1]["lines"])
        green = pr.patch_report({"bad": {"site_host_unreached": "x"}}, {"fail_on_could_not": False})
        self.assertFalse(any("ends failed" in x for x in green["sections"][-1]["lines"]))

    def test_every_host_failed(self):
        r = pr.patch_report({"a": {"_patch_could_not": {"reason": "LOST mid-run", "step": "the update", "detail": "x"}},
                             "b": {"_patch_could_not": {"reason": "FAILED during update", "step": "the update", "detail": "y"}}}, {})
        self.assertEqual(r["title"], "Patch hosts: 0 hosts patched, 2 could not be patched")
        self.assertEqual(r["needs_look"], ["a", "b"])

    def test_as_the_other_reports(self):
        """The email's text, the workbook's sheets and the website's counts read it as any other report."""
        r = pr.patch_report(hosts(), {})
        text = sf.report_text(r)
        self.assertIn("Could not patch or needs a look", text)
        sheets = sf.report_xlsx_sheets(r)
        self.assertIn("Could not patch or needs a look", [s.get("name") or s.get("title") for s in sheets])
        for s in r["sections"]:
            if "rows" in s:
                self.assertTrue(all(len(x) == len(s["columns"]) for x in s["rows"]), s["title"])
                self.assertEqual(len(s.get("row_status", s["rows"])), len(s["rows"]))
        facts = pr.patch_host_facts({"h": {"patch_started": True, "ansible_host": "192.0.2.1", "_patch_done": DONE}}, ["h", "gone"])
        self.assertEqual(facts, {"h": {"patch_started": True, "_patch_done": DONE}, "gone": {}})



class ByHand(unittest.TestCase):
    """1.74.1: patch_by_hand_hosts / patch_by_hand_groups - listed with what the read-only checks found, never a failure;
    patch_reboot_allowed false - the reboot is left to a person."""
    def hosts(self):
        return {"web01.example.mil": {"_patch_done": DONE, "patch_started": True},
                "db01.example.mil": {"patch_by_hand": True, "patch_updates": 7, "patch_reboot_pending": True},
                "db02.example.mil": {"patch_by_hand": True, "patch_updates": 0, "patch_reboot_pending": False},
                "db03.example.mil": {"patch_by_hand": True, "site_host_unreached": "No route to host"},
                "db04.example.mil": {"patch_by_hand": True, "patch_not_ready": "only 200 MB free in /var (need 1024 MB)"}}

    def test_section_and_tile(self):
        r = pr.patch_report(self.hosts(), {})
        self.assertEqual((r["status"], r["needs_look"], r["counts"]["critical"]), ("ok", [], 0))     # never a failure
        self.assertEqual(r["title"], "Patch hosts: 1 host patched, 4 to patch by hand")
        self.assertEqual([(t["label"], t["value"]) for t in r["summary"]][-2:], [("Skipped on purpose", 0), ("Patch by hand", 4)])
        sec = [x for x in r["sections"] if x["title"] == "Patch by hand"][0]
        self.assertEqual(sec["columns"], ["Host", "Updates pending", "Reboot pending", "What to do"])
        self.assertEqual(sec["rows"], [
            ["db01.example.mil", "7", "REBOOT PENDING", "patch this one by hand"],
            ["db02.example.mil", "0", "none pending", "patch this one by hand"],
            ["db03.example.mil", "not checked", "-", "not reached - check by hand"],
            ["db04.example.mil", "not checked", "-", "not ready (only 200 MB free in /var (need 1024 MB)) - check by hand"]])
        self.assertEqual([c[2:] for c in sec["cell_status"]], [["warning", ""], ["", ""], ["", "warning"], ["", "warning"]])
        self.assertNotIn("Could not patch or needs a look", [x["title"] for x in r["sections"]])
        self.assertFalse(any("ends failed" in x for x in r["sections"][-1]["lines"]))

    def test_a_dry_run_lists_them_too(self):
        r = pr.patch_report(self.hosts(), {"dry": True})
        self.assertIn("Patch by hand", [x["title"] for x in r["sections"]])
        self.assertTrue(r["title"].startswith("DRY RUN - Patch hosts: 1 host would be patched, 4 to patch by hand"))

    def test_by_hand_wins_over_its_problem(self):
        st = pr.patch_host_state({"patch_by_hand": True, "patch_unsupported": "Ubuntu 24.04, apt"})
        self.assertEqual((st["state"], st["updates"]), ("by_hand", "no dnf or yum"))
        self.assertEqual(pr.patch_host_state({"patch_by_hand": True})["updates"], "not known (the check did not answer)")
        # 1.74.3: sudo refused - not checked, with sudo's words; never a failure
        st = pr.patch_host_state({"patch_by_hand": True, "patch_not_ready": "cannot become root: sudo: a password is required"})
        self.assertEqual((st["state"], st["updates"], st["todo"]),
                         ("by_hand", "not checked", "not checked - sudo refused (sudo: a password is required)"))

    def test_reboot_by_hand(self):
        done = dict(DONE, rebooted=False, reboot=pr.REBOOT_BY_HAND)
        r = pr.patch_report({"web01": {"_patch_done": done}}, {"reboot_allowed": False, "automatic_restarts": True})
        patched = [x for x in r["sections"] if x["title"] == "Patched"][0]
        self.assertEqual(patched["rows"][0][2], "Reboot needed - reboot by hand (patch_reboot_allowed is false)")
        self.assertEqual(patched["cell_status"][0][2], "warning")
        self.assertIn("reboots off (patch_reboot_allowed is false)", r["subtitle"])


class BatchBad(unittest.TestCase):
    def test_lost_or_could_not(self):
        hv = {"a": {"_patch_df": {"unreachable": True, "msg": "x"}}, "b": {"_patch_could_not": {"reason": "FAILED during update"}},
              "c": {"_patch_df": {"rc": 0}, "_patch_dnf": {"skipped": True}}, "d": {}}
        self.assertEqual(pr.patch_batch_bad(hv, ["a", "b", "c", "d", "e"]), ["a", "b"])
        self.assertEqual(pr.patch_batch_bad(hv, []), [])

    def test_behind_the_checkpoint(self):
        """A host that failed before the checkpoint has no _patch_could_not yet (its rescue runs after its peers' last
        step): it is the one whose _patch_at is not this checkpoint's."""
        hv = {"ok": {"_patch_at": "before the install"}, "failed": {"_patch_at": "before the snapshot"},
              "purpose": {"_patch_at": "the start", "_patch_ended_on_purpose": "never-patch group"},
              "stopped": {"_patch_at": "before the snapshot", "patch_not_started": "the rollout stopped at x"},
              "never": {}}
        self.assertEqual(pr.patch_batch_bad(hv, list(hv), "before the install"), ["failed"])
        self.assertEqual(pr.patch_batch_bad(hv, list(hv)), [])                       # without a checkpoint: as before


if __name__ == "__main__":
    unittest.main()
