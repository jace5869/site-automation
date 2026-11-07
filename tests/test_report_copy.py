#!/usr/bin/env python3
"""Unit tests for roles/site_email/library/site_report_copy.py: the copy job's checkpoint. A job AAP did not answer
for (a 503 on its detail page) is looked at again by the next run - never passed over. Needs ansible-core (the
module's imports): python3 tests/test_report_copy.py"""
import datetime
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "roles", "site_email", "library"))
for name in ("site_aap", "site_report_page"):
    _spec = importlib.util.spec_from_file_location("ansible.module_utils." + name, os.path.join(HERE, "..", "plugins", "module_utils", name + ".py"))
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["ansible.module_utils." + name] = _mod
    _spec.loader.exec_module(_mod)
import site_report_copy as m  # noqa: E402

NOW = datetime.datetime(2026, 11, 7, 12, 0, 0)


def t(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000000Z")


class FakeAap(object):
    """jobs: {id: (finished, detail status code)}. The list answers every job finished after finished__gt."""
    def __init__(self, jobs):
        self.jobs, self.detail_calls = jobs, []

    def call(self, method, path):
        if path.startswith("jobs/?"):
            since = path.split("finished__gt=")[1].split("&")[0]
            rows = [{"id": i, "finished": fin, "job_template": 1, "status": "successful"} for i, (fin, _) in sorted(self.jobs.items())
                    if m.parse_time(fin) > m.parse_time(since)]
            return 200, {"results": rows, "next": None}
        i = int(path.split("/")[1])
        self.detail_calls.append(i)
        code = self.jobs[i][1]
        return code, ({"id": i, "name": "job %d" % i, "artifacts": {}} if code == 200 else {"detail": "Service Unavailable"})


def params(**kw):
    p = {"jobs": [], "template_id": 0, "job_id": 0, "hours": 24, "max_pages": 50, "overlap_minutes": 5}
    p.update(kw)
    return p


class Checkpoint(unittest.TestCase):
    def test_an_unanswered_job_is_looked_at_again(self):
        aap = FakeAap({10: (t(60), 200), 11: (t(40), 503), 12: (t(20), 200)})
        r = m.find(aap, params(), NOW)
        self.assertEqual(aap.detail_calls, [10, 11, 12])
        self.assertTrue(any("job 11: AAP answered HTTP 503" in w for w in r["warnings_text"]))
        self.assertEqual(r["until"], m.iso(m.parse_time(t(40))))      # the next run starts no later than job 11
        self.assertEqual(r["seen"], [12])                             # 10 is before the edge, 11 was not read
        # the next run: AAP answers now - 11 is read, 12 (seen) is passed over, 10 is not listed again
        aap.jobs[11] = (t(40), 200)
        aap.detail_calls = []
        m.find(aap, params(), NOW)             # as the next run would, from the checkpoint: finished after until - overlap
        self.assertIn(11, aap.detail_calls)

    def test_all_read_the_checkpoint_is_the_newest(self):
        aap = FakeAap({10: (t(60), 200), 12: (t(20), 200)})
        r = m.find(aap, params(), NOW)
        self.assertEqual((r["until"], r["seen"], r["warnings_text"]), (m.iso(m.parse_time(t(20))), [12], []))


if __name__ == "__main__":
    unittest.main()
