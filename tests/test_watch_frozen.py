#!/usr/bin/env python3
"""1.116.0: the VM outage watch's scheduled path is frozen at 1.118.4 (what the user runs). The review is built on the watch's
own conditions and never changes how a scheduled run reads vCenter, judges a VM, builds its time line, or opens and clears
tickets. Changing what is pinned here is a watch release of its own (with its own tests), not part of the review.
  python3 tests/test_watch_frozen.py"""
import copy
import hashlib
import importlib.util
import os
import sys
import unittest

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "plugins", "filter"), os.path.join(ROOT, "tests")]
FROZEN = {   # sha256 of 1.118.4 (084e5931)
    "roles/vmware_vm/library/site_vmware_watch.py": "d2e47963d3e749b5813869970aab4edb2f7dc4c620b5d8de2810577570dba001",
    "plugins/filter/vmware_watch_filters.py": "53e81f37e68ce543ef43a07fdb677252f8797bb82b89e63de705e9d207daa979",
    # 1.118.4's plugins/filter/outage_timeline_filters.py, word for word: a scheduled run's time line is made by it
    "plugins/module_utils/outage_timeline_1184.py": "245830afda1069456ca5301ae551de68bbbf13b3ce9ad95aa792d8e7e6c3cdb1",
    # 1.118.4's roles/vmware_vm/tasks/watch.yml, the reference the task comparison below reads
    "tests/fixtures/watch_1184.yml": "66889c040b95d9749d8fcb768c9e82e1941698ee30b6cfc10668b799d11c25bd",
}
# The tasks of 1.118.4's watch.yml that differ at 1.116.0, each with why a scheduled run (vm_watch_review false) does exactly
# what it did. Any other difference fails.
ALLOWED = {
    "VMware | what is down or hung (the suspects checked {{ vm_watch_confirm_checks }} time(s), {{ vm_watch_confirm_seconds }} s apart)":
        "no_log: vm_watch_review - false on a scheduled run",
    "VMware | vCenter's events of the window": "review: omit unless a review; no_log: false unless a review",
    "VMware | the tickets of the window (ServiceNow, GET only; skipped without the ServiceNow API credential)":
        "review: omit unless a review; no_log: false unless a review",
    "VMware | the time line": "opts gain job and review (false) and combine({}) - outage_timeline hands review-false calls to 1.118.4's code",
    "VMware | the time line was not made (the watch goes on without it)": "the message is 1.118.4's unless a review",
    "VMware | the AAR draft's facts (vm_outage_review.aar)": "inside the review-only AAR block",
    "VMware | a temporary folder for the AAR draft": "renamed inside the review-only AAR block",
    "VMware | the AAR draft - {{ _rd_file.filename | default('not made') }}": "inside the review-only AAR block",
    "VMware | the report": "_vm_w_aar_info gains review-only keys and review; outage_review_aar hands review-false info to 1.118.4's code",
    "VMware | outage report": "every new value is guarded by _vm_w_review, or is '' / false, which site_email treats as unset",
}


def _flat(tasks, out):
    for t in tasks or []:
        out.append(t)
        for k in ("block", "rescue", "always"):
            if k in t:
                _flat(t[k], out)
    return out


class Frozen(unittest.TestCase):
    def test_1_files_are_1_118_4(self):
        for path, want in FROZEN.items():
            with open(os.path.join(ROOT, path), "rb") as fh:
                self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), want, "%s changed: the watch is frozen at 1.118.4" % path)

    def test_2_watch_yml_differs_only_where_allowed(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "watch_1184.yml")) as fh:
            old = _flat(yaml.safe_load(fh), [])
        with open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "watch.yml")) as fh:
            new = dict((t.get("name"), t) for t in _flat(yaml.safe_load(fh), []))
        strip = lambda t: dict((k, v) for k, v in t.items() if k not in ("block", "rescue", "always"))   # noqa: E731
        changed = set(t.get("name") for t in old if t.get("name") not in new or strip(new[t["name"]]) != strip(t))
        self.assertEqual(sorted(changed - set(ALLOWED)), [], "a scheduled-path task of watch.yml changed")
        # the order of 1.118.4's tasks is kept
        names = [n for n in (t.get("name") for t in _flat(yaml.safe_load(open(os.path.join(ROOT, "roles", "vmware_vm", "tasks", "watch.yml"))), []))
                 if n in set(t.get("name") for t in old)]
        self.assertEqual(names, [t.get("name") for t in old if t.get("name") in names])

    def test_3_a_scheduled_time_line_is_1_118_4s(self):
        import outage_timeline_filters as ot
        spec = importlib.util.spec_from_file_location("ot1184", os.path.join(ROOT, "plugins", "module_utils", "outage_timeline_1184.py"))
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        import test_outage_timeline as tot
        events, tickets, opts = tot.director_outage()
        # the time line opts a scheduled run passes (watch.yml): review false, plus the job; nothing of a review
        sched = dict(opts, review=False, job="5120")
        for evs in (events,
                    # the 1.116.0 check's case: an APD raise, and another device's clear 30 s later - 1.118.4 gives one Low row
                    [dict(time=1000, vcenter="vc01", host="esx01", type="esx.problem.storage.apd.start",
                          message="Device naa.1111 has entered the All Paths Down state"),
                     dict(time=1030, vcenter="vc01", host="esx01", type="esx.clear.storage.apd.exit",
                          message="Device naa.2222 has exited the All Paths Down state")]):
            o = dict(sched) if evs is events else dict(begin=900, end=1200, review=False, job="5120")
            got = ot.outage_timeline(copy.deepcopy(evs), copy.deepcopy(tickets), copy.deepcopy(o))
            want = old.outage_timeline(copy.deepcopy(evs), copy.deepcopy(tickets), copy.deepcopy(o))
            self.assertEqual(got, want)
            doc = copy.deepcopy(tot.WATCH)
            for web in (False, True):
                self.assertEqual(ot.outage_timeline_doc(copy.deepcopy(doc), got, web, False), old.outage_timeline_doc(copy.deepcopy(doc), want, web, False))
            info = {"file": "", "error": "", "job": "5120", "csv_file": "", "noc_file": "", "noc_error": "", "asked": False, "draft": False,
                    "review": False}
            self.assertEqual(ot.outage_review_aar(copy.deepcopy(doc), info, True), old.outage_review_aar(copy.deepcopy(doc), info, True))
        self.assertEqual(ot.outage_window(10000, 6, "", "", ""), old.outage_window(10000, 6, "", "", ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
