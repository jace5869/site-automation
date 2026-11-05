#!/usr/bin/env python3
"""Write a small STIG benchmark in DISA's XCCDF 1.1 shape (what STIG Manager imports, POST /stigs), for the test against
a real STIG Manager (tests/stig/run_stigman_real_test.sh): RHEL_9_STIG V2R7 with the STIG rules of make_results.py's
SCAP content, and one manual check no scan answers. A later release (RELEASE 8) with rules DISA changed (OLD=NEW: the
rule's new id, and a check of other words - STIG Manager keeps a review of each apart):
  make_stig_xccdf.py OUT.xml [RELEASE] [SV-257835r1044874_rule=SV-257835r1099999_rule,...]"""
import sys

from make_results import RULES

NS = "http://checklists.nist.gov/xccdf/1.1"
MANUAL = ("SV-258999r1000001_rule", "V-258999", "RHEL-09-999990", "medium", "A manual check: a document, never scanned.")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def rules():
    seen, out = set(), []
    for _name, sev, svs, vs, stigids, title in RULES:
        for sv, v, sid in zip(svs.split(), vs.split(), stigids.split()):
            if sv not in seen:
                seen.add(sv)
                out.append((sv, v, sid, sev, title))
    return out + [MANUAL]


def group(sv, v, sid, sev, title, check="Check it as the STIG says."):
    desc = esc("<VulnDiscussion>%s</VulnDiscussion><FalsePositives></FalsePositives><FalseNegatives></FalseNegatives>"
               "<Documentable>false</Documentable><Mitigations></Mitigations><SeverityOverrideGuidance></SeverityOverrideGuidance>"
               "<PotentialImpacts></PotentialImpacts><ThirdPartyTools></ThirdPartyTools><MitigationControl></MitigationControl>"
               "<Responsibility></Responsibility><IAControls></IAControls>" % esc(title))
    fix = "F-%s_fix" % sv.split("r")[0][3:]
    return ('<Group id="%s"><title>SRG-OS-000480-GPOS-00227</title><description>&lt;GroupDescription&gt;&lt;/GroupDescription&gt;</description>'
            '<Rule id="%s" weight="10.0" severity="%s"><version>%s</version><title>%s</title><description>%s</description>'
            '<ident system="http://cyber.mil/cci">CCI-000366</ident><fixtext fixref="%s">Set it as the STIG says.</fixtext><fix id="%s" />'
            '<check system="C-%s_chk"><check-content-ref href="Red_Hat_Enterprise_Linux_9_STIG.xml" name="M" />'
            '<check-content>%s</check-content></check></Rule></Group>'
            % (v, sv, sev, sid, esc(title), desc, fix, fix, sv.split("r")[0][3:], esc(check)))


def write(out, release=7, changed=None, bench="RHEL_9_STIG", title="Red Hat Enterprise Linux 9 Security Technical Implementation Guide",
          rule_list=None, version=2):
    """The benchmark (another one with bench, title, rule_list: [(rule id, V-number, version, severity, title)]: the checklist import's test)."""
    changed = changed or {}
    rule_list = rule_list or rules()
    body = "".join(group(changed[r[0]], *r[1:], check="Check it as the STIG's release %d says." % release) if r[0] in changed else group(*r)
                   for r in rule_list)
    xml = ('<?xml version="1.0" encoding="utf-8"?><Benchmark xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" '
           'xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'id="%s" '
           'xml:lang="en" xmlns="%s"><status date="2025-07-24">accepted</status><title>%s</title><description>A test STIG for site-automation.</description>'
           '<notice id="terms-of-use" xml:lang="en"></notice><front-matter xml:lang="en"></front-matter>'
           '<rear-matter xml:lang="en"></rear-matter><plain-text id="release-info">Release: %d Benchmark Date: %s'
           '</plain-text><plain-text id="generator">3.5</plain-text><plain-text id="conventionsVersion">1.10.0</plain-text>'
           '<version>%d</version><Profile id="MAC-1_Classified"><title>I - Mission Critical Classified</title>'
           '<description>&lt;ProfileDescription&gt;&lt;/ProfileDescription&gt;</description>%s</Profile>%s</Benchmark>'
           % (bench, NS, esc(title), release, "24 Jul 2025" if release == 7 else "23 Oct 2025", version,
              "".join('<select idref="%s" selected="true" />' % r[1] for r in rule_list), body))
    with open(out, "w") as f:
        f.write(xml)


if __name__ == "__main__":
    write(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 7,
          dict(x.split("=", 1) for x in sys.argv[3].split(",") if x) if len(sys.argv) > 3 else None)
