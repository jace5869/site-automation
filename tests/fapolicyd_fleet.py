"""A made-up fleet for the fapolicyd denials report (tests/test_denials_report.py, the report fit documents): each host's variables as
ACT's playbook (vendor/act/ansible/playbooks/fapolicyd_denials.yml) leaves them, with the ausearch -i text in _fa_denials."""
import json, os, sys
MD = """Several programs were blocked while they were not in the trust database.
### Likely causes
1. **The monitoring agent** was updated and its binary changed.
2. A backup script runs from /var/tmp.
### Findings
**Finding 1: monitoring agent not trusted**
* **ROOT CAUSE:** the agent binary is not in the trust database after the update.
* **FIX:** add it with `fapolicyd-cli --file add` after review."""
EV = """----
type=PATH msg=audit(11/15/2026 {t}:00.000:{n}) : item=0 name={path} inode=50 obj=unconfined_u:object_r:usr_t:s0 nametype=NORMAL
type=SYSCALL msg=audit(11/15/2026 {t}:00.000:{n}) : arch=x86_64 syscall=execve success=no exit=EPERM ppid=1 pid={n} uid=root comm=x exe={exe} subj=unconfined_u:unconfined_r:unconfined_t:s0
type=FANOTIFY msg=audit(11/15/2026 {t}:00.000:{n}) : resp=deny fan_type=rule_info fan_info=13 subj_trust=yes obj_trust=no
"""
HOSTS = {
    "web01.example.mil": [("/usr/bin/bash", "/opt/monitor/agent", 14, "09:12"), ("/usr/bin/python3", "/var/tmp/backup.py", 6, "08:40")],
    "web02.example.mil": [("/usr/bin/bash", "/opt/monitor/agent", 9, "09:15")],
    "db01.example.mil": [("/usr/bin/bash", "/opt/monitor/agent", 11, "09:01"), ("/usr/bin/sh", "/srv/db/tools/vacuum.sh", 3, "07:30")],
    "app-server-long-name-07.subdomain.example.mil": [("/usr/bin/python3", "/var/tmp/backup.py", 22, "09:20"), ("/usr/bin/bash", "/opt/monitor/agent", 5, "06:10")],
    "app02.example.mil": [("/usr/bin/java", "/opt/app/lib/plugin.jar", 4, "05:55")],
    "lin05.example.mil": [],
    "lin06.example.mil": [],
}
def hv(host):
    ds = HOSTS[host]
    out, n = [], 100
    for exe, path, count, t in ds:
        for _ in range(count):
            n += 1
            out.append(EV.format(t=t, n=n, path=path, exe=exe))
    total = sum(c for _, _, c, _ in ds)
    d = {"_fa_rpm": {"rc": 0}, "_fa_active": {"stdout": "active"}, "_fa_auditd": {"stdout": "active"}, "_fa_count": str(total),
         "_fa_paths": [p for _, p, _, _ in ds], "_fa_exes": sorted(set(e for e, _, _, _ in ds)),
         "_fa_findings": ["fapolicyd denied %d access attempt(s) since today (%d distinct file(s))" % (total, len(ds))] if ds else [],
         "_fa_denials": {"stdout": "".join(out), "rc": 0 if out else 1}}
    if ds:
        d["act_triage_result"] = {"status": "done", "summary": MD, "denied": [{"command": "fapolicyd-cli --file add /opt/monitor/agent", "pre_approvable": True}]}
    if host == "db01.example.mil":
        d["_fa_auditd"] = {"stdout": "inactive"}
    return d
def hostvars():
    names = list(HOSTS)
    return [hv(h) for h in names], names
