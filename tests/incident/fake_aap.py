#!/usr/bin/env python3
"""A fake AAP for tests/incident/run_incident_test.sh: the SOP job's template (job template 2, its survey as the setup
job writes it) behind tests/test_email_question.py's FakeAap (token goodtoken). 1.112.0: three jobs for the AAR from an
outage review (incident_review) - 4714, a VM - outage watch review with its AAR draft in the artifact (the outage of
tests/test_outage_timeline.py); 4720, a newer watch run without a review; 4730, another job.

  fake_aap.py URLFILE

Writes its address to URLFILE, then serves until killed. GET .../job_templates/2/survey_spec/ shows the draft."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "plugins", "filter"))
from test_email_question import FakeAap, _jt  # noqa: E402
import incident_doc_filters as f  # noqa: E402
import test_outage_timeline as T  # noqa: E402

aap = FakeAap()
aap.jobs = [_jt(1, "Outage AAR", "playbooks/incident_aar.yml"), _jt(2, "SOP - Word document", "playbooks/incident_sop.yml", enabled=True)]
aap.specs = {("job", 1): {}, ("job", 2): {"name": "", "description": "", "spec": f.incident_survey("sop")}}
r = T.made()
draft = f.incident_review_draft(r["draft"], None, {"job": "4714", "made": "2026-11-12 02:45 UTC"})
# Neon P1 (1.112.0): text from a ticket that looks like a template stays text all the way into the AAR
draft["actions"].append("12 Nov 03:30 CET - Work note in INC0012345: Template error: {{ lookup('ansible.builtin.env', 'HOME') }}")
JOBS = {4714: {"id": 4714, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml",
               "artifacts": {"vm_outage_review": dict(r["numbers"], review=True, aar=draft)}},
        4720: {"id": 4720, "name": "VM - outage watch", "playbook": "playbooks/vm_outage_watch.yml",
               "artifacts": {"vm_outage_review": dict(r["numbers"], review=False)}},
        4730: {"id": 4730, "name": "Health check", "playbook": "playbooks/health_check.yml", "artifacts": {}}}
plain = aap.answer


def answer(method, rest, qs, body, path):
    if rest and rest[0] == "jobs" and method == "GET":
        if len(rest) == 1:
            end = qs.get("playbook__endswith", "")
            hits = [dict((k, v) for k, v in j.items() if k != "artifacts") for i, j in sorted(JOBS.items(), reverse=True)
                    if j["playbook"].endswith(end)]
            return 200, {"count": len(hits), "next": None, "results": hits}
        j = JOBS.get(int(rest[1])) if rest[1].isdigit() else None
        return (200, j) if j else (404, {"detail": "Not found."})
    return plain(method, rest, qs, body, path)


aap.answer = answer
with open(sys.argv[1], "w") as fh:
    fh.write(aap.url)
while True:
    time.sleep(3600)
