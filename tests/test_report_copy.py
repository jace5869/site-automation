#!/usr/bin/env python3
"""Unit tests for roles/site_email/library/site_report_copy.py: the copy job's checkpoint. A job AAP did not answer
for (a 503 on its detail page) is looked at again by the next run - never passed over. The jobs whose page the last
copy could not write (its site_report_copied failed) are tried again first and named, without holding back where the
run gets to (1.24.0). Needs ansible-core (the module's imports): python3 tests/test_report_copy.py"""
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



def report(job, when):
    """A report kept for the copy job (site_report_web with keep), packed."""
    page = "vm/2026-11-07_job-%d.html" % job
    doc = {"files": {"page": page, "latest": "vm/latest.html", "index": "index.html"}, "url": "", "footer": "",
           "data": {"report": {"title": "t", "sections": []}, "job": job, "template": "VM health", "when": when},
           "entry": {"key": "vm/latest.html", "when": when, "job": job}}
    return sys.modules["ansible.module_utils.site_report_page"].pack(doc, 1 << 20)[0]


class FakeCopyAap(object):
    """jobs: {id: {finished, template, artifacts, code}} - the copy template's earlier runs, the list, each job's detail."""
    def __init__(self, jobs):
        self.jobs, self.detail_calls = jobs, []

    def call(self, method, path):
        q = dict(x.split("=", 1) for x in path.split("?", 1)[1].split("&")) if "?" in path else {}
        if "job_template" in q:                            # this template's earlier runs, newest first
            rows = [{"id": i} for i, j in sorted(self.jobs.items(), reverse=True)
                    if j["template"] == int(q["job_template"]) and i < int(q["id__lt"])]
            return 200, {"results": rows}
        if "finished__gt" in q:
            rows = [{"id": i, "finished": j["finished"], "job_template": j["template"]} for i, j in sorted(self.jobs.items())
                    if m.parse_time(j["finished"]) > m.parse_time(q["finished__gt"])]
            return 200, {"results": rows, "next": None}
        i = int(path.split("/")[1])
        self.detail_calls.append(i)
        j = self.jobs.get(i)
        if j is None:
            return 404, {"detail": "Not found."}
        if j.get("code", 200) != 200:
            return j["code"], {"detail": "Service Unavailable"}
        return 200, {"id": i, "name": "VM health", "finished": j["finished"], "status": "successful", "artifacts": j.get("artifacts", {})}


class FailedPages(unittest.TestCase):
    def aap(self):
        failed = [{"job": 11, "why": "uploading https://web/r/x.html: HTTP 413 Request Entity Too Large"},
                  {"job": 12, "why": "access denied"}, {"job": 13, "why": "x"}, {"job": 21, "why": "y"}]
        return FakeCopyAap({
            11: {"finished": t(600), "template": 7, "artifacts": {"site_report_page": [report(11, "2026-11-07 02:00 UTC")]}},
            12: {"finished": t(500), "template": 7, "code": 503},
            21: {"finished": t(20), "template": 7, "artifacts": {"site_report_page": [report(21, "2026-11-07 11:40 UTC")]}},
            22: {"finished": t(10), "template": 7, "artifacts": {"site_report_page": [report(22, "2026-11-07 11:50 UTC")]}},
            400: {"finished": t(30), "template": 90, "artifacts": {"site_report_copied": {"until": t(30), "seen": [], "failed": failed}}}})

    def test_tried_again_first_named_never_holding_back(self):
        aap = self.aap()
        r = m.find(aap, params(template_id=90, job_id=401), NOW)
        self.assertEqual([x["job"] for x in r["reports"]], [11, 21, 22])          # 11 long before the window, 21 read once
        self.assertEqual(aap.detail_calls.count(21), 1)
        self.assertEqual([x["job"] for x in r["retry"]], [12])                    # AAP did not answer: the next run again
        self.assertEqual(r["retry"][0]["why"], "access denied")
        self.assertEqual(len(r["retrying"]), 4)
        self.assertIn("job 11: its page was not copied by the last copy (uploading https://web/r/x.html: HTTP 413", r["retrying"][0])
        self.assertTrue(any(w.startswith("job 13: AAP answered HTTP 404") for w in r["warnings_text"]))   # gone: not again
        self.assertEqual(r["until"], m.iso(m.parse_time(t(10))))                 # the failed jobs never hold it back

    def test_what_a_copy_kept(self):
        self.assertEqual(m._failed({"failed": [11, "12", {"job": 13, "why": " a\n b "}, {"job": 11}, {"job": "x"}, None]}),
                         [(11, "it failed"), (12, "it failed"), (13, "a b")])
        self.assertEqual(m._failed({"failed": "11"}), [])
        self.assertEqual(m._failed({}), [])
        aap = self.aap()
        self.assertEqual(m.last_copy(aap, 90, 401)[2][0], (11, "uploading https://web/r/x.html: HTTP 413 Request Entity Too Large"))


if __name__ == "__main__":
    unittest.main()
