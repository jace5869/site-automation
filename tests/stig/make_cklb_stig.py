#!/usr/bin/env python3
"""Write an Evaluate-STIG checklist (CKLB) of ONE STIG with the rules given - for the STIG answer keys' many-host test
(1.76.0: RHEL 8, 9 and 10 stand-ins on one box).
  make_cklb_stig.py OUT.cklb STIG_ID VERSION '[{"sv": "SV-230221", "status": "open", "title": "...", "severity": "medium"}, ...]'
A status is not_a_finding, open, not_applicable or not_reviewed. Only the rules Evaluate-STIG was asked for (its
--SelectVuln, in SELECT - a file, one V- id a line - when given) are written, as Evaluate-STIG writes them."""
import json
import os
import re
import sys

NAMES = {"RHEL_8_STIG": "Red Hat Enterprise Linux 8", "RHEL_9_STIG": "Red Hat Enterprise Linux 9", "RHEL_10_STIG": "Red Hat Enterprise Linux 10"}


def main(out, stig, version, rules, select=""):
    v, r = re.match(r"V(\d+)R(\d+)", version).groups()
    want = set(x.strip() for x in open(select).read().replace(",", "\n").split() if x.strip()) if select and os.path.isfile(select) else None
    rows = []
    for x in rules:
        n = x["sv"].split("-")[1]
        if want is not None and "V-" + n not in want:
            continue
        rows.append({"rule_id": "%sr1_rule" % x["sv"], "group_id": "V-" + n, "rule_version": x.get("version", "X-00-" + n),
                     "severity": x.get("severity", "medium"), "rule_title": x.get("title", "Rule " + n), "status": x["status"],
                     "finding_details": "Found: not as required." if x["status"] == "open" else "", "comments": ""})
    name = NAMES.get(stig, stig)
    with open(out, "w") as f:
        json.dump({"title": "Evaluate-STIG", "cklb_version": "1.0", "target_data": {"host_name": "HOST"},
                   "stigs": [{"stig_name": name + " Security Technical Implementation Guide", "display_name": name, "stig_id": stig,
                              "version": v, "release_info": "Release: %s Benchmark Date: 30 Sep 2026" % r, "rules": rows}]}, f)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4]), *(sys.argv[5:6]))
