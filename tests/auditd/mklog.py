#!/usr/bin/env python3
"""Write a fake auditd setup for tests/auditd/run_auditd_test.sh: auditd.conf and audit.log files
(the current one and rotated ones, oldest records in the highest number, mtimes like auditd's).

  mklog.py DIR NOW SPEC.json

SPEC: {"num_logs": 5, "max_log_file": 8, "action": "ROTATE", "files": 2,
       "sources": [{"auid": 1500, "events": 800, "hours": 20, "kind": "syscall"|"user",
                    "key": "delete", "exe": "/usr/bin/rm", "pad": 0, "enriched": false}]}
Each event is written the way auditd writes it: SYSCALL + EXECVE + CWD + PATH + PROCTITLE records
for a syscall rule, one USER_CMD record for sudo. Events are spread evenly over the last `hours`.
`pad` adds that many bytes to the PROCTITLE record (to make big files fast)."""
import json
import os
import sys

out, now, spec = sys.argv[1], int(sys.argv[2]), json.load(open(sys.argv[3]))
os.makedirs(out, exist_ok=True)
GS = "\x1d"   # ENRICHED log format: the interpreted fields follow this separator


def event(ts, serial, s):
    a = s["auid"]
    stamp = "msg=audit(%d.%03d:%d):" % (ts, serial % 1000, serial)
    name = s.get("name", "uid%s" % a)
    if s.get("kind", "syscall") == "user":
        rec = ("type=USER_CMD %s pid=4242 uid=%s auid=%s ses=7 subj=unconfined_u:unconfined_r:unconfined_t:s0 "
               "msg='cwd=\"/home/x\" cmd=6C73202D6C61 exe=\"/usr/bin/sudo\" terminal=pts/0 res=success'" % (stamp, a, a))
        if s.get("enriched"):
            rec += '%sUID="%s" AUID="%s"' % (GS, name, name)
        return [rec]
    key = s.get("key")
    k = '"%s"' % key if key else "(null)"
    sysc = ("type=SYSCALL %s arch=c000003e syscall=263 success=yes exit=0 a0=ffffff9c a1=55d a2=0 a3=0 items=2 ppid=4241 pid=4242 "
            "auid=%s uid=0 gid=0 euid=0 suid=0 fsuid=0 egid=0 sgid=0 fsgid=0 tty=pts0 ses=7 comm=\"x\" exe=\"%s\" "
            "subj=unconfined_u:unconfined_r:unconfined_t:s0 key=%s" % (stamp, a, s.get("exe", "/usr/bin/true"), k))
    if s.get("enriched"):
        sysc += '%sARCH=x86_64 SYSCALL=unlinkat AUID="%s" UID="root" GID="root" EUID="root"' % (GS, name)
    return [sysc,
            # an argument that looks like an auid= field must not change the event's account
            'type=EXECVE %s argc=3 a0="rm" a1="-f" a2="auid=999"' % stamp,
            'type=CWD %s cwd="/tmp"' % stamp,
            'type=PATH %s item=0 name="/tmp/x" inode=1 dev=fd:00 mode=0100644 ouid=0 ogid=0 rdev=00:00 nametype=DELETE' % stamp,
            "type=PROCTITLE %s proctitle=726D002D66%s" % (stamp, "0" * s.get("pad", 0))]


evs = []
serial = 1000
for s in spec["sources"]:
    n, hours = s["events"], s.get("hours", 24)
    for i in range(n):
        ts = now - int(hours * 3600) + int((i + 0.5) * hours * 3600 / n)
        serial += 1
        evs.append((ts, serial, s))
evs.sort(key=lambda e: (e[0], e[1]))

nfiles = max(1, spec.get("files", 1))
chunks = [evs[i * len(evs) // nfiles:(i + 1) * len(evs) // nfiles] for i in range(nfiles)]
log = os.path.join(out, "audit.log")
for i, chunk in enumerate(chunks):            # chunk 0 = the oldest = audit.log.<nfiles-1>
    n = nfiles - 1 - i
    path = log if n == 0 else "%s.%d" % (log, n)
    with open(path, "w") as f:
        for ts, ser, s in chunk:
            f.write("\n".join(event(ts, ser, s)) + "\n")
    last = chunk[-1][0] if chunk else now
    os.utime(path, (last, last if n else now))
with open(os.path.join(out, "auditd.conf"), "w") as f:
    f.write("#\n# This file controls the configuration of the audit daemon\n#\n\nlocal_events = yes\n"
            "log_file = %s\nlog_format = ENRICHED\nmax_log_file = %s\nnum_logs = %s\nmax_log_file_action = %s\n"
            "space_left = 25%%\nspace_left_action = email\nadmin_space_left = 50\nadmin_space_left_action = single\n"
            % (log, spec.get("max_log_file", 8), spec.get("num_logs", 5), spec.get("action", "ROTATE").lower()))
