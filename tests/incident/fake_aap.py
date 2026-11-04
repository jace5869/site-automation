#!/usr/bin/env python3
"""A fake AAP for tests/incident/run_incident_test.sh: the SOP job's template (job template 2, its survey as the setup
job writes it) behind tests/test_email_question.py's FakeAap (token goodtoken).

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

aap = FakeAap()
aap.jobs = [_jt(1, "Outage AAR", "playbooks/incident_aar.yml"), _jt(2, "SOP - Word document", "playbooks/incident_sop.yml", enabled=True)]
aap.specs = {("job", 1): {}, ("job", 2): {"name": "", "description": "", "spec": f.incident_survey("sop")}}
with open(sys.argv[1], "w") as fh:
    fh.write(aap.url)
while True:
    time.sleep(3600)
