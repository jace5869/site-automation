#!/usr/bin/env python3
"""Write a small Evaluate-STIG checklist - CKLB (JSON, as STIG Viewer 3 and STIG Manager read it) or CKL (XML) - for
the STIG drift tests: the STIG rules of make_results.py, each with the status given, plus another STIG (Firefox) in
the same file, as Evaluate-STIG's combined checklists have.
  make_cklb.py OUT.cklb '{"SV-257985": "open", ...}' [V2R10] [cklb|ckl]
A rule not given is not_a_finding; a status is not_a_finding, open, not_applicable or not_reviewed. An open rule's
finding details say what was found."""
import json
import os
import re
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_results  # noqa: E402

# what Evaluate-STIG writes in an Open rule's finding details: its own header, a hash, separators around what it found
FOUND = ("Evaluate-STIG 1.2607.0 (Scan-RHEL9_Checks) found this to be OPEN on 10/30/2026\nResultHash: " + "0f" * 32
         + "\n~~~~~~~~~~~~~~~~~~~~\nFound: %s is not set as required.\n----------------------------------------")
CKL = {"not_a_finding": "NotAFinding", "open": "Open", "not_applicable": "Not_Applicable", "not_reviewed": "Not_Reviewed"}


def rules(results):
    out, seen = [], set()
    for _scap, sev, svs, vs, ids, title in make_results.RULES:
        for i, sv in enumerate(svs.split()):
            key = re.match(r"SV-\d+", sv).group(0)
            if key in seen:
                continue
            seen.add(key)
            st = results.get(key, "not_a_finding")
            out.append({"rule_id": sv, "group_id": vs.split()[i], "rule_version": ids.split()[i], "severity": sev, "rule_title": title,
                        "status": st, "finding_details": (FOUND % key) if st == "open" else "Compliant."})
    out.append({"rule_id": "SV-258999r1000001_rule", "group_id": "V-258999", "rule_version": "RHEL-09-999990", "severity": "medium",
                "rule_title": "A manual check: a document.", "status": results.get("SV-258999", "not_reviewed"), "finding_details": ""})
    return out


def write(out, results, version="V2R10", fmt="cklb"):
    v, r = re.match(r"V(\d+)R(\d+)", version).groups()
    rs = rules(results)
    stigs = [{"stig_name": "Red Hat Enterprise Linux 9 Security Technical Implementation Guide", "display_name": "Red Hat Enterprise Linux 9",
              "stig_id": "RHEL_9_STIG", "version": v, "release_info": "Release: %s Benchmark Date: 30 Sep 2026" % r, "rules": rs},
             {"stig_name": "Mozilla Firefox Security Technical Implementation Guide", "display_name": "Mozilla Firefox",
              "stig_id": "MOZ_Firefox_STIG", "version": "6", "release_info": "Release: 6 Benchmark Date: 30 Jul 2026",
              "rules": [{"rule_id": "SV-251545r879887_rule", "group_id": "V-251545", "rule_version": "FFOX-00-000001", "severity": "high",
                         "rule_title": "Firefox must be a supported version.", "status": "open", "finding_details": "old"}]}]
    if fmt == "cklb":
        with open(out, "w") as f:
            json.dump({"title": "Evaluate-STIG", "cklb_version": "1.0", "target_data": {"host_name": "WEB01"}, "stigs": stigs}, f)
        return
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<CHECKLIST><ASSET><HOST_NAME>WEB01</HOST_NAME></ASSET><STIGS>']
    for s in stigs:
        parts.append("<iSTIG><STIG_INFO>")
        for k, val in (("version", s["version"]), ("stigid", s["stig_id"]), ("releaseinfo", s["release_info"]), ("title", s["stig_name"])):
            parts.append("<SI_DATA><SID_NAME>%s</SID_NAME><SID_DATA>%s</SID_DATA></SI_DATA>" % (k, escape(val)))
        parts.append("</STIG_INFO>")
        for x in s["rules"]:
            parts.append("<VULN>")
            for k, val in (("Vuln_Num", x["group_id"]), ("Severity", x["severity"]), ("Rule_ID", x["rule_id"]), ("Rule_Ver", x["rule_version"]),
                           ("Rule_Title", x["rule_title"]), ("CCI_REF", "CCI-000001"), ("CCI_REF", "CCI-000002")):
                parts.append("<STIG_DATA><VULN_ATTRIBUTE>%s</VULN_ATTRIBUTE><ATTRIBUTE_DATA>%s</ATTRIBUTE_DATA></STIG_DATA>" % (k, escape(val)))
            parts.append("<STATUS>%s</STATUS><FINDING_DETAILS>%s</FINDING_DETAILS></VULN>" % (CKL[x["status"]], escape(x["finding_details"])))
        parts.append("</iSTIG>")
    parts.append("</STIGS></CHECKLIST>\n")
    with open(out, "w") as f:
        f.write("".join(parts))


if __name__ == "__main__":
    write(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}, *(sys.argv[3:5]))
