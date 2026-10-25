#!/usr/bin/env python3
"""Write a small XCCDF 1.2 results file in the shape oscap writes (the benchmark with its STIG profile and rules,
then the TestResult), for the STIG drift tests: the rules below, each with the result given.
  make_results.py OUT.xml '{"sshd_disable_root_login": "fail", ...}' [stig-version]
A rule not given passes. Two SCAP rules (the two audit ones) check one STIG rule, as in the real content."""
import json
import sys

RULES = [  # SCAP rule, severity, SV id with its revision, V id, STIG id, title
    ("sshd_disable_root_login", "medium", "SV-257985r1069364_rule", "V-257985", "RHEL-09-255045", "RHEL 9 must not permit direct logons to the root account using remote access through SSH."),
    ("ensure_gpgcheck_local_packages", "high", "SV-257821r1015077_rule", "V-257821", "RHEL-09-214020", "RHEL 9 must check the GPG signature of locally installed software packages."),
    ("audit_rules_privileged_commands_sudo", "medium", "SV-258176r1045388_rule", "V-258176", "RHEL-09-654225", "RHEL 9 must audit all uses of the sudo command."),
    ("audit_rules_privileged_commands_su", "medium", "SV-258176r1045388_rule", "V-258176", "RHEL-09-654225", "RHEL 9 must audit all uses of the su command."),
    ("accounts_tmout", "medium", "SV-257883r1044925_rule", "V-257883", "RHEL-09-412035", "RHEL 9 must automatically exit interactive command shell user sessions after 10 minutes of inactivity."),
    ("package_telnet-server_removed", "high", "SV-257835r1044874_rule", "V-257835", "RHEL-09-215040", "RHEL 9 must not have the telnet-server package installed."),
    ("banner_etc_issue", "medium", "SV-257779r958390_rule", "V-257779", "RHEL-09-211020", "RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner."),
    ("enable_authselect", "medium", "", "", "", "Enable authselect"),
]
NS = "http://checklists.nist.gov/xccdf/1.2"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def write(out, results, version="V2R9"):
    rules = []
    for name, sev, sv, v, stigid, title in RULES:
        refs = ""
        if stigid:
            refs += '<reference href="https://www.cyber.mil/stigs/downloads/?_dl_facet_stigs=operating-systems%%2Cunix-linux">%s</reference>' % stigid
        if sv:
            refs += '<reference href="https://www.cyber.mil/stigs/srg-stig-tools/">%s</reference>' % sv
            refs += '<reference href="https://www.cyber.mil/stigs/srg-stig-tools#vulnerability_id">%s</reference>' % v
        rules.append('<Rule id="xccdf_org.ssgproject.content_rule_%s" selected="false" severity="%s"><title>%s</title>'
                     '<reference href="https://www.example.org/controls/">5</reference>%s</Rule>' % (name, sev, esc(title), refs))
    rr = []
    for name, sev, _, _, _, _ in RULES:
        rr.append('<rule-result idref="xccdf_org.ssgproject.content_rule_%s" role="full" severity="%s" weight="1.000000"><result>%s</result></rule-result>'
                  % (name, sev, results.get(name, "pass")))
    rr.append('<rule-result idref="xccdf_org.ssgproject.content_rule_not_in_profile" role="full" severity="low"><result>notselected</result></rule-result>')
    doc = ('<?xml version="1.0" encoding="UTF-8"?>\n<Benchmark xmlns="%s" id="xccdf_org.ssgproject.content_benchmark_RHEL-9">'
           '<title>Guide to the Secure Configuration of Red Hat Enterprise Linux 9</title>'
           '<Profile id="xccdf_org.ssgproject.content_profile_stig"><title>DISA STIG for Red Hat Enterprise Linux 9</title>'
           '<description>This profile contains configuration checks that align to the DISA STIG for Red Hat Enterprise Linux 9 %s.</description></Profile>'
           '<Profile id="xccdf_org.ssgproject.content_profile_cis"><title>CIS Red Hat Enterprise Linux 9 Benchmark</title><description>V2.0.0</description></Profile>'
           '<Group id="xccdf_org.ssgproject.content_group_system"><title>System Settings</title>%s</Group>'
           '<TestResult id="xccdf_org.open-scap_testresult_xccdf_org.ssgproject.content_profile_stig" start-time="2026-10-24T18:19:27+00:00" '
           'end-time="2026-10-24T18:19:29+00:00" version="0.1.82" test-system="cpe:/a:redhat:openscap:1.3.14">'
           '<benchmark href="ssg-rhel9-ds.xml" id="xccdf_org.ssgproject.content_benchmark_RHEL-9"/><title>OSCAP Scan Result</title>'
           '<profile idref="xccdf_org.ssgproject.content_profile_stig"/><target>web01.example.mil</target><target-address>192.0.2.10</target-address>'
           '%s</TestResult></Benchmark>\n') % (NS, version, "".join(rules), "".join(rr))
    with open(out, "w") as f:
        f.write(doc)


if __name__ == "__main__":
    write(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}, sys.argv[3] if len(sys.argv) > 3 else "V2R9")
