#!/usr/bin/env python3
"""Made-up data for the STIG compliance scorecard's tests (tests/test_stig_scorecard.py, tests/stig/run_scorecard_test.sh):
three STIG Manager collections with their open findings as they are today, and the same collections' points of the weeks
before, as the earlier jobs of the checklist import's template kept them.

  scorecard_fixture.py sm OUT.json                the stand-in STIG Manager's data (tests/stig/stigman_mock.py)
  scorecard_fixture.py jobs OUT.json NOW [WEEKS]  the fake AAP's jobs (tests/stig/fake_aap_jobs.py); NOW: 2026-11-13T06:00:00Z

Today: SITEA - RHEL 9 (6 assets) 1 CAT I, 8 CAT II, 2 CAT III; SITEA - VMware (3) 3 CAT II; SITEA - Windows Server (4)
3 CAT I, 4 CAT II, 1 CAT III. Against the run a week ago: RHEL 9 closed 4 and opened 1 (Better), Windows Server opened 1
CAT I (Worse), VMware the same. Two weeks ago and before, RHEL 9 had one more rule open (SV-257900) on a host more each
week; VMware has points for 4 weeks only. The drift reports' newest jobs found 3 rules fixed on their hosts; 2 are still
Open now."""
import calendar
import copy
import json
import sys
import time

TEMPLATE = 140                  # the checklist import's job template in the fake AAP
ARIA = "VMW_Aria_Operations_8-x_Apache_STIG"
RULES = {   # SV-number: (rule id, STIG ID, severity, title)
    "SV-257777": ("SV-257777r991589_rule", "RHEL-09-211010", "high", "RHEL 9 must be a vendor-supported release."),
    "SV-257985": ("SV-257985r1069364_rule", "RHEL-09-255045", "medium", "RHEL 9 must not allow a root login over SSH."),
    "SV-258036": ("SV-258036r1045099_rule", "RHEL-09-291010", "medium", "RHEL 9 must have the USBGuard package installed."),
    "SV-257900": ("SV-257900r991589_rule", "RHEL-09-232040", "medium", "RHEL 9 cron configuration directories must have a mode of 0700."),
    "SV-257842": ("SV-257842r991589_rule", "RHEL-09-215075", "low", "RHEL 9 must not have the tuned package installed."),
    "SV-254239": ("SV-254239r958478_rule", "WN22-00-000030", "high", "Windows Server 2022 passwords for the built-in account must change."),
    "SV-254247": ("SV-254247r991589_rule", "WN22-00-000110", "high", "Windows Server 2022 must be maintained at a supported servicing level."),
    "SV-254269": ("SV-254269r958478_rule", "WN22-00-000330", "medium", "Windows Server 2022 must have a host-based firewall."),
    "SV-254285": ("SV-254285r958478_rule", "WN22-AC-000010", "low", "Windows Server 2022 account lockout duration must be 15 minutes."),
    "SV-256375": ("SV-256375r885906_rule", "ESXI-70-000001", "medium", "Access to the ESXi host must be limited by lockdown mode."),
    "SV-256379": ("SV-256379r885918_rule", "ESXI-70-000005", "medium", "The ESXi host must enforce an unlock timeout of 15 minutes."),
}
CAT = {"high": 1, "medium": 2, "low": 3}
COLLECTIONS = [("1", "SITEA - VMware", ["vrops02", "esx01", "esx02"]),
               ("2", "SITEA - RHEL 9", ["rhel01", "rhel03", "rhel04", "rhel05", "rhel06", "rhel07"]),
               ("3", "SITEA - Windows Server", ["win01", "win03", "win04", "win05"])]
NOW = {"1": {"SV-256375": ["esx01", "esx02"], "SV-256379": ["esx01"]},
       "2": {"SV-257777": ["rhel04"], "SV-257985": ["rhel01", "rhel03", "rhel05"],
             "SV-258036": ["rhel01", "rhel03", "rhel04", "rhel05", "rhel06"], "SV-257842": ["rhel03", "rhel07"]},
       "3": {"SV-254239": ["win03"], "SV-254247": ["win03", "win04"], "SV-254269": ["win01", "win03", "win04", "win05"],
             "SV-254285": ["win01"]}}


def state(weeks_ago=0):
    """{collection id: {SV-number: [asset names]}} as it was that many weeks ago (0 = today)."""
    s = copy.deepcopy(NOW)
    if weeks_ago >= 1:
        s["2"]["SV-257985"] += ["rhel04", "rhel06", "rhel07"]
        s["2"]["SV-258036"] += ["rhel07"]
        s["2"]["SV-257842"] = ["rhel03"]
        s["3"]["SV-254247"] = ["win03"]
    if weeks_ago >= 2:
        s["2"]["SV-257900"] = COLLECTIONS[1][2][:min(6, weeks_ago - 1)]
    if weeks_ago >= 4:
        del s["1"]                                  # the collection was made later
    return s


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def seconds(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def point(s, read_at):
    """A run's point (the artifact stig_scorecard) of a state."""
    cols = []
    for cid, name, assets in COLLECTIONS:
        if cid not in s:
            continue
        rec = {"id": cid, "name": name, "assets": len(assets), "cat1": 0, "cat2": 0, "cat3": 0, "r1": {}, "r2": {}, "r3": {}}
        for sv, hosts in s[cid].items():
            cat = CAT[RULES[sv][2]]
            rec["cat%d" % cat] += len(hosts)
            rec["r%d" % cat][sv] = len(hosts)
        cols.append(rec)
    return {"v": 1, "read_at": read_at, "collections": cols}


def stigman():
    """The stand-in STIG Manager's data: today's state as reviews with the result fail; SITEA - VMware has vrops02 with
    the Aria Operations Apache STIG and two reviews, so the checklists of tests/stig/ckl can be imported there."""
    def review(sv, result="fail", status="saved"):
        rule, version, severity, title = RULES[sv]
        return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-11-01T10:00:00Z", "comment": "",
                "detail": "", "rule": {"ruleId": rule, "version": version, "severity": severity, "title": title}}

    def aria(n, result, status):
        rule = "SV-2600%02dr1_rule" % n
        return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-10-01T10:00:00Z", "comment": "",
                "detail": "", "rule": {"ruleId": rule, "version": "VRPE-8X-%06d" % n, "severity": "medium", "title": "x"}}
    assets, reviews, n = {}, {}, 20
    for cid, name, names in COLLECTIONS:
        assets[cid] = []
        for a in names:
            n += 1
            assets[cid].append({"assetId": str(n), "name": a, "fqdn": a + ".example.mil",
                                "stigs": [{"benchmarkId": ARIA, "revisionStr": "V1R1"}] if a == "vrops02" else []})
            reviews[str(n)] = [review(sv) for sv, hosts in sorted(NOW[cid].items()) if a in hosts]
            if a == "vrops02":
                reviews[str(n)] += [aria(1, "pass", "submitted"), aria(2, "pass", "accepted")]
            if a == "rhel01":                       # a rule that passes is no finding
                reviews[str(n)].append(review("SV-257777", "pass", "submitted"))
    return {"secret": "s3cret", "client": "autostig", "writers": ["stigwrite"], "managers": ["stigwrite"],
            "collections": [{"collectionId": cid, "name": name} for cid, name, names in COLLECTIONS],
            "assets": assets, "reviews": reviews,
            "titles": {ARIA: "VMware Aria Operations 8.x Apache Server Security Technical Implementation Guide"},
            "latest": {ARIA: "V1R1"},
            "rules": {ARIA: [{"ruleId": "SV-2600%02dr1_rule" % i, "groupId": "V-2600%02d" % i, "version": "VRPE-8X-%06d" % i,
                              "severity": "medium", "title": "Aria Operations Apache rule %d" % i} for i in (1, 2, 5, 7, 9)]}}


def jobs(now, weeks=8):
    """The fake AAP's jobs, oldest first: a run of the import's template each week (the week's point), one 3 days ago, a
    failed one without a point, a job of another template; and the drift reports' newest jobs with what they found fixed."""
    t = seconds(now)
    out, jid = [], 4000

    def job(when, template, name, artifacts, status="successful"):
        return {"id": jid, "job_template": template, "name": name, "status": status, "finished": stamp(when + 40), "artifacts": artifacts}
    for k in range(weeks, 0, -1):
        jid += 1
        when = t - k * 7 * 86400 + 120
        out.append(job(when, TEMPLATE, "STIG Manager - checklist import", {"stig_scorecard": point(state(k), stamp(when))}))
    jid += 1
    when = t - 3 * 86400
    mid = state(0)
    mid["2"]["SV-257985"] += ["rhel04"]
    out.append(job(when, TEMPLATE, "STIG Manager - checklist import", {"stig_scorecard": point(mid, stamp(when))}))
    jid += 1
    out.append(job(t - 2 * 86400, TEMPLATE, "STIG Manager - checklist import", {}, "failed"))
    jid += 1
    out.append(job(t - 86400, 99, "Health check", {"stig_scorecard": point(state(5), stamp(t - 86400))}))
    jid += 1
    scanned = stamp(t - 86400)
    out.append(job(t - 86400, 141, "STIG drift report", {"stig_fixed_open": {"v": 1, "read_at": scanned, "more": 0, "assets": [
        ["SITEA - RHEL 9", "rhel01"], ["SITEA - RHEL 9", "rhel03"]], "rows": [
        ["rhel01.example.mil", "SITEA - RHEL 9", "rhel01", "SV-257985", "CAT II", RULES["SV-257985"][3], scanned],
        ["rhel03.example.mil", "SITEA - RHEL 9", "rhel03", "SV-257900", "CAT II", RULES["SV-257900"][3], scanned]]}}))
    jid += 1
    out.append(job(t - 86400, 142, "Windows STIG drift report", {"stig_fixed_open": {"v": 1, "read_at": scanned, "more": 0, "assets": [
        ["SITEA - Windows Server", "win03"]], "rows": [
        ["win03.corp.example.mil", "SITEA - Windows Server", "win03", "SV-254239", "CAT I", RULES["SV-254239"][3], scanned]]}}))
    return out


if __name__ == "__main__":
    if sys.argv[1] == "sm":
        json.dump(stigman(), open(sys.argv[2], "w"))
    else:
        json.dump(jobs(sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 8), open(sys.argv[2], "w"))
