# The runbooks: what each one checks, and what to do about it

For each runbook: what it answers, its settings (defaults in `roles/<role>/defaults/main.yml`;
change them in your settings files or AAP's Variables boxes, as [VARIABLES.md](VARIABLES.md)
shows), and each finding with what usually fixes it. The `look:` command
printed with a finding is always the first thing to run.

## Health check (`playbooks/health_check.yml`)

Runs the checks you pick on each host: a list of names, or the shortcuts:

- `daily` = disk, mounts, services, performance, time, network, logging, mariadb (the database check: MariaDB or MySQL; `mysql` and `database` also pick it), containers (podman containers, rootful and rootless)
- `weekly` = selinux, fapolicyd, auditd, accounts, certs, patching
- `all` = both

A host with a finding at a severity in `site_fail_on` (default: critical and warning) fails. That
is what a workflow reacts to. A template that only emails its report to people: `site_fail_on: []`
keeps the job green ([EMAIL_REPORTS.md](EMAIL_REPORTS.md)). A check that itself breaks becomes a warning finding (`the X check
could not run: ...`, id `<check>:check-error`) and the other checks still run. That finding fails
the host even when `site_fail_on` is `[critical]` (a check that did not run is not a healthy
result); only `site_fail_on: []` never fails.

### disk

| Finding | Means | Usually |
|---|---|---|
| `/var is 96% full` | local filesystem space (`check_disk_warn_pct` 85, `check_disk_crit_pct` 95) | find what grew with the `look:` command: old logs, core dumps, a runaway log file. `journalctl --vacuum-size=500M` for the journal |
| `... has used 97% of its inodes` | too many small files (space may still be free) | usually a directory with millions of small files (session/cache/spool); `look:` finds it |
| `/var will be full in about 3.2 days` | growth-rate forecast from earlier runs (needs a day of history) | same as above, before it becomes an outage |
| `could not read filesystem usage` | `df` failed or timed out | a hung mount; see the mounts check |

Per-mount limits: `check_disk_overrides: {/var/log/audit: {warn: 70, crit: 85}}`. The forecast
keeps a small history in `/var/lib/site-health/disk-history.json` (the only file the checks write;
a Check-mode run does not write it).

### mounts

| Finding | Means | Usually |
|---|---|---|
| `/data (xfs) is in /etc/fstab but is not mounted` | it did not mount at boot, or was unmounted | `journalctl -b` for the mount error; a missing disk or LUN; a typo in fstab |
| `/var is mounted READ-ONLY` | the kernel remounted it read-only after disk/filesystem errors | **urgent**: `journalctl -k` for I/O errors; storage team; plan an fsck |
| `network mount /mnt/share does not answer within 5s` | NFS/CIFS server down, network, or a stale handle | check the server and the network; diagnose only (NetApp is vendor-managed) |
| `network mount ... is 96% full` | the share is full | the storage owner |

### services

| Finding | Means | Usually |
|---|---|---|
| `systemd unit X has FAILED` | a unit crashed or exited with an error | `systemctl status X`, `journalctl -u X`; fix the cause, then `systemctl restart X` |
| `required service X is inactive` | a service that must run (`check_services_required` + `_extra`) is stopped | same; find out why it stopped before starting it |
| `required service X is not installed` | the unit does not exist here | remove it from the list for this group, or install it |
| `required service X is disabled` | it will not start after a reboot | `systemctl enable X` |
| `X has restarted 5 times` | a restart loop: it keeps crashing | its log shows the crash |

Per group: `check_services_required_extra: [mariadb]`. To ignore a failed unit:
`check_services_ignore_failed: ['^dnf-makecache']`.

### performance

| Finding | Means | Usually |
|---|---|---|
| `15-minute load is 14.2 on 4 CPUs (3.55 per CPU)` | more work than CPUs, for 15 minutes | the top CPU processes are in the finding; a runaway job, or undersized |
| `only 4% of memory is available` | memory is nearly exhausted | the top memory processes are in the finding; a leak, or undersized |
| `63% of swap is in use` | the host is short of memory | as above |
| `CPUs spend 35% of their time waiting for disk` | slow or overloaded storage | `iostat -x` shows which device; storage latency |
| `the hypervisor takes 15% of CPU time (steal)` | the VM host is overcommitted | the virtualization team |
| `the kernel killed processes for lack of memory` | OOM kills in the last 24 h (with the victims) | the victim's memory limit, or the host's memory |
| `27 zombie processes` | a parent process is not reaping its children | restart the parent (`look:` shows it) |

### time

| Finding | Means | Usually |
|---|---|---|
| `the clock is NOT synchronized` | chrony uses no time source | `chronyc sources -v`: unreachable servers, firewall (UDP 123) |
| `chrony is installed but not answering` | chronyd is not running | `systemctl start chronyd` |
| `the clock is 2.4s ahead of NTP time` | offset above `check_time_offset_warn` | chrony corrects it slowly; very large offsets break Kerberos/AD logins at 5 minutes |

### network

| Finding | Means | Usually |
|---|---|---|
| `there is no default route` | the host cannot reach other networks | NetworkManager connection, gateway |
| `this host's own name ... does not resolve` | its FQDN is not in DNS or /etc/hosts | DNS record |
| `ldap.example.mil does not resolve (DNS)` | a name in `check_network_dns_names` fails | `/etc/resolv.conf`, DNS server |
| `LDAP (ldap.example.mil:636) does not answer within 5s` | a TCP port in `check_network_tcp` is unreachable **from this host** | firewall (local or network), the service down, routing |

### logging

| Finding | Means | Usually |
|---|---|---|
| `the journal is kept in memory only` | `/var/log/journal` missing: logs vanish at reboot | `mkdir /var/log/journal && systemctl restart systemd-journald` (or `Storage=persistent`) |
| `log server SIEM (...) does not answer` | forwarding target unreachable | network/firewall; the SIEM collector |
| `rsyslog has 850 MB of undelivered messages queued` | the log server has been unreachable for a while | as above; the queue drains once it answers |
| `1203 error messages in the journal` | something complains a lot (top senders listed) | look at the top sender first |

### selinux

| Finding | Means | Usually |
|---|---|---|
| `SELinux is Permissive, it must be enforcing` | not enforcing (STIG) | `setenforce 1` after checking denials; `SELINUX=enforcing` in `/etc/selinux/config` |
| `/etc/selinux/config says SELINUX=permissive` | a reboot would change the mode | fix the config file |
| `locally added permissive domain(s)` | SELinux only logs for those domains | `semanage permissive -d <domain>` once the policy is right |
| `12 SELinux denial(s) since yesterday - top: nginx (httpd_t) denied read on a file labeled user_home_t` | SELinux blocked something | a wrong label (fix: `restorecon -Rv <path>`), a boolean (`setsebool -P ...`), or a real policy need. `audit2why` says which |
| `SELinux boolean X is off, expected on` | a boolean in `check_selinux_booleans` differs | `setsebool -P X on` |
| `N file(s) under /etc/nginx have the wrong SELinux label` | a dry-run `restorecon` would relabel them (`check_selinux_relabel_paths`) | `restorecon -Rv <path>` |

### fapolicyd

| Finding | Means | Usually |
|---|---|---|
| `fapolicyd is inactive` | application allow-listing is off | `systemctl start fapolicyd`; its log says why it stopped |
| `fapolicyd is in permissive mode` | it logs but blocks nothing | `permissive = 0` in `/etc/fapolicyd/fapolicyd.conf` |
| `N file(s) no longer match the fapolicyd trust database` | files changed outside RPM | `fapolicyd-cli --update`, or re-add the file's trust entry |
| `N fapolicyd denial(s) since yesterday - top: /usr/bin/bash -> /opt/app/tool` | it blocked programs | a legitimate program needs a trust entry (`fapolicyd-cli --file add <path>`), after review |

`check_fapolicyd_required: true` (the example inventory sets it) makes a missing fapolicyd a
finding.

### auditd

| Finding | Means | Usually |
|---|---|---|
| `auditd is failed` | nothing is being audited | `systemctl status auditd`; often a full audit log partition |
| `auditing is DISABLED in the kernel` | `auditctl -s` says enabled 0 | `audit=1` on the kernel command line |
| `only 1 audit rule(s) are loaded` | the STIG rules are missing or failed to load | `augenrules --check`, `augenrules --load`; a syntax error in `/etc/audit/rules.d/` |
| `N audit event(s) were LOST` | the kernel backlog overflowed | raise `-b` (backlog) in the rules |
| `the audit log filesystem is 92% full` | auditd may stop, or halt the host, when it fills | rotate/offload; check `space_left_action` |
| `audit records are not forwarded` | au-remote is not active (`check_auditd_remote_required`) | configure audisp-remote |
| `audit volume is 850 MB a day (... events in 24 h, at most N in one hour) - mostly account svc_x (60%), rule key delete (55%), program /usr/bin/python3 (40%)` | more audit records than `check_auditd_volume_warn_mb_day` (200), excluded accounts not counted | what the named account/program does that the named STIG rule records: a job deleting temp files in a loop, an app in a rootless container (its processes keep the user's login ID) |
| `the audit logs on disk reach back only 9.5 h (5 files of 8 MB, then the oldest is deleted)` | with `max_log_file_action = ROTATE` older records are gone (`check_auditd_retention_warn_hours` 24) | bigger `max_log_file` / more `num_logs` in `/etc/audit/auditd.conf`, or fewer records (the finding names who makes them) |
| `auditd dropped records N time(s) ... on the way to its plugins` | the queue to au-remote / the SIEM forwarder was full: the SIEM copy is missing records | fewer records; a larger `q_depth` in `auditd.conf` |
| `audit rule(s) stop recording auid=1234 (someone) altogether` | a `-a never` (or `-a always,exclude`) rule hides an account that is not in `check_auditd_exclude_accounts` | remove the rule, or approve the account (below) |
| `too much to break down by account within 120 s` | very large logs | `aureport` with the `look:` command, or a larger `check_auditd_volume_timeout` |

**How much is audited, and the vulnerability scanner.** The volume, retention and rule findings
come from the audit logs themselves. The breakdown (which account, which STIG rule key, which
program) is read only when the volume or the retention is over its limit, at low priority, and is
always printed in the job output (`Auditd | how much is audited`), with excluded accounts shown
separately.

A credentialed vulnerability scan logs in to every host and runs thousands of commands with sudo:
during a scan its account usually makes most of the audit records. List it in
`check_auditd_exclude_accounts` (in `playbooks/group_vars/all.yml`, so it applies to every host) and
it no longer counts toward the volume warning. It still counts for retention and disk space, which
are physical: if the scan rotates a week of records away in an hour, that is still reported, with
the scanner named as the cause. The job output shows how much of the scanner's volume comes from
syscall rules and how much from sudo/PAM records, which tells you what an audit rule exclusion would
remove.

To stop auditd recording that account at all (a host change, not part of the health check):
- It is a deliberate exception to the STIG audit requirements: get it approved and recorded first.
- The rules `-a never,exit -F arch=b64 -S all -F auid=<UID>` and the same with `arch=b32` (the form
  of audit's own sample rules), placed before the STIG rules, remove the syscall records: a file
  like `/etc/audit/rules.d/05-exclude-scanner.rules` (`augenrules` merges the files in name order).
  The sudo/PAM records (USER_CMD, USER_START, CRED_*) come from user space and need
  `-a never,user -F auid=<UID>`. Leaving that one out keeps the sudo records, which still show
  what the account ran.
- Use the numeric UID. A name is looked up when the rules load at boot, before IdM/AD (sssd) can
  answer, and a rule that fails to load can stop the rest of the rules from loading.
- STIG hosts make the rules immutable (`-e 2`): a new rule takes effect only at the next reboot.
- The auditd check then reports rules like this only for accounts that are NOT on
  `check_auditd_exclude_accounts`.

### accounts

| Finding | Means | Usually |
|---|---|---|
| `account(s) other than root have UID 0` | full root power under another name | **investigate first**; remove or renumber the account |
| `account(s) with an EMPTY password` | log in without a password | `passwd -l <user>` |
| `3 account(s) not used for 35+ days` | inactive and not locked (STIG) | lock them (`usermod -L`), or list break-glass accounts in `check_accounts_ignore_inactive` |
| `sudo without a password (NOPASSWD) for: alice` | a sudoers entry skips the password | remove it, or allow it on purpose in `check_accounts_nopasswd_allowed` (e.g. `svc_aap`) |
| `the password of svc_aap expires in 6 days` | an account in `check_accounts_expiry_watch` expires soon | **change it before it breaks automation** (and update the Machine credential) |

### certs

| Finding | Means | Usually |
|---|---|---|
| `certificate CN=... in /etc/stigman/stigman.crt expires in 9 days` | a certificate file expires within `check_certs_crit_days` (critical) / `warn_days` (warning) | renew it. For STIG Manager: update the *TLS certificate* credential and run *STIG Manager - deploy*; it restarts only nginx |
| `... in STIG Manager (https://127.0.0.1:443) expires in ...` | the certificate a TLS endpoint serves | the service's certificate, even if the file on disk was already replaced (restart the service) |
| `... in /opt/.../agent_keystore (alias mid) expires in ...` | a Java keystore entry | renew in the keystore (MID Server: ServiceNow's procedure) |
| `could not check the certificate in X` | unreadable file, endpoint not answering, or a PKCS12 keystore without its password | fix the path, or add the Keystore password credential |

What it looks at: `check_certs_files` (globs), the usual directories (`check_certs_discover_dirs`,
skipping CA bundles), `check_certs_endpoints`, `check_certs_keystores`. For a PKCS12 keystore, name
the variable that holds the password (`password_env: KEYSTORE_PASSWORD`), never the password.

### patching

| Finding | Means | Usually |
|---|---|---|
| `12 security advisories pending, 2 of them rated Critical` | updates that fix security issues are waiting | patch (the *Patch with checks* workflow) |
| `a reboot is needed to finish earlier updates` | new kernel or core libraries are not in use yet | reboot in a window |
| `no package has been updated for 60 days` | older than `check_patching_max_age_days` | patch |
| `cannot check for updates` | dnf cannot reach its repositories (Satellite, mirror) | repository configuration, network, subscription |

<a id="mariadb"></a>

### mariadb and mysql (health only): the database check

Checks **MariaDB and MySQL** (5.7, 8.0, 8.4; MariaDB 10.x and 11.x; Percona). Pick it in the Health
check as `mariadb`, `mysql` or `database` (the same check), or run it alone with the **Database
health - MariaDB / MySQL** template (`playbooks/database_health.yml`: only this check, on every
database server it is pointed at; it fails a host that has findings, so a workflow can open a
ticket). In the Health check it runs on hosts in `mariadb_hosts`, `mysql_hosts`, `database_hosts`,
`mariadb` or `mysql`, and on any host that has a container name set. It runs status queries only:
no data is read and nothing is changed. It handles a database installed on the host and one in
**podman containers** (one, several, found automatically, or rootless): the client inside each
container is used. **How to set it up, step by step, and how to verify it: [MARIADB.md](MARIADB.md).**

| Finding | Means | Usually |
|---|---|---|
| `MariaDB is not running` | the service or container is down | its log; disk full; a crash |
| `MariaDB is running but the status query failed` | cannot log in, or it hangs | the login (see below); `max_connections` reached |
| `MariaDB restarted 12 minutes ago` | a restart nobody may have planned | the error log: crash, OOM kill |
| `143 of 151 connections in use (95%)` | clients are about to be refused | the `look:` query shows which user and client hold them; a connection leak in an app |
| `N client connection(s) were REFUSED` | `max_connections` was reached since the last start | as above; raise `max_connections` only if the load is real |
| `40 queries are executing at this moment` | a pile-up: locking or one slow query | `look:` lists the oldest |
| `2 query/queries running longer than 300s` | long-running queries (user / db / seconds / state; never the query text) | the owner decides; `KILL <id>` only after that |
| `replication (default) is broken: IO thread No` | a replica stopped copying | `Last_IO_Error` / `Last_SQL_Error` in the finding |
| `replica ... is 1900 seconds behind` | replication lag | load on the replica; long transactions on the primary |
| `Galera node is not healthy` / `cluster has 2 node(s), expected 3` | Galera cluster trouble | the node's error log; network between nodes |
| `MySQL Group Replication is not healthy` / `has 2 ONLINE member(s), expected 3` | a Group Replication member is not ONLINE, or the group is short (`check_mariadb_group_size`) | `SELECT ... FROM performance_schema.replication_group_members` (in the finding); the member's error log |
| `could not read the replication status` / `... Group Replication members` | the monitoring account lacks a privilege | the grant named in the finding ([MARIADB.md](MARIADB.md#4-the-login-the-mariadb-monitor-credential)) |
| `only 91% of reads come from memory` | `innodb_buffer_pool_size` may be too small | the DBA |
| `3 [ERROR] line(s) in the MariaDB log` | errors in its log in the last day (latest shown) | read them |
| `the MariaDB data directory is on a filesystem 93% full` | MariaDB stops writing when it fills | disk space |

**Logging in** (the full steps are in [MARIADB.md](MARIADB.md#4-the-login-the-mariadb-monitor-credential)). Without a credential, the check logs in as root through the local socket. That
is MariaDB's default for a host install on RHEL (no password needed with sudo); MySQL's root
usually has a password. **A database in a container usually gives root a password** (the official images do), and the check then reports
`Access denied ... attach the "MariaDB monitor" credential`. For containers, or whenever you prefer
not to use root, ask the DBA to create a monitoring account with **no data access**, inside that
database with host `localhost`, and attach the *MariaDB monitor* credential to the Health check
template:

```sql
CREATE USER 'aap_monitor'@'localhost' IDENTIFIED BY '<password>';
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'aap_monitor'@'localhost';   -- MySQL; MariaDB before 10.5.9
GRANT PROCESS, REPLICA MONITOR ON *.* TO 'aap_monitor'@'localhost';      -- MariaDB 10.5.9 and newer instead
GRANT SELECT ON performance_schema.* TO 'aap_monitor'@'localhost';       -- MySQL Group Replication only
```

`PROCESS` shows other sessions (for long queries). Replication status needs `REPLICATION CLIENT`
(MySQL) or, on MariaDB 10.5.9 and newer, `REPLICA MONITOR`: **`REPLICATION CLIENT` alone is not
enough there.** The password reaches the client only through its environment, never its command line.

<a id="containers"></a>

### containers: podman containers, root's and every user's

Finds the podman containers on the host by itself - you list nothing - and checks each one.
Hosts without podman are skipped (`skipped: podman is not installed`, no finding). It is
read-only: it runs `podman ps`, one `podman container inspect` and `systemctl show`, and nothing
that starts, stops or creates anything.

**Which containers.** Root's (rootful podman) and each user's (rootless podman: only that user
can see them). A user is looked at when a podman process runs as them, when they have *linger* on
(a file in `/var/lib/systemd/linger/`), or when they are in `/etc/subuid` and have container
storage in their home (`~/.local/share/containers/storage`). A user's containers are read **as
that user** (`runuser -u USER -- env XDG_RUNTIME_DIR=/run/user/UID podman ...`), and only when
`/run/user/UID` exists: otherwise podman would create it, and a check must not change anything.
Such a user is listed as `not checked` instead (and reported, below).

**Which should be running.** A container should be running when a systemd unit runs it and
starts it at boot (a Quadlet `.container` file with an `[Install]` section, or an enabled unit
from `podman generate systemd` or written by hand), or when its restart policy is `always` or
`unless-stopped`. A container that has neither and stopped with exit code 0 was stopped on
purpose: no finding. A Quadlet or `--new` unit removes its container while the unit is stopped,
so the check also reads the Quadlet files and unit files: a container that should run but does
not exist at all is found too (state `missing`).

The output has a table of what was found:

```text
OWNER        NAME                       STATE              UNIT                           SHOULD RUN
root         systemd-db                 running            db.service (active)            yes
root         web                        running            -                              yes
alice        web                        missing            web.service (failed)           yes
bob          web                        exited (3)         -                              no
not checked: carol - no /run/user/1003 (the user is not logged in and linger is off): ...
```

Every finding names the owner, and its `look:` command runs as that owner (paste it as root):

| Finding | Means | Usually |
|---|---|---|
| `container X (owner O) should be running (...) but it does not exist / it exited with code N` (critical) | a container that should run is down, or its systemd unit failed | read the `look:` output (the unit's status, the container's log, the journal). Start it the right way: `systemctl start UNIT` for a unit (as a user: `runuser -u O -- env XDG_RUNTIME_DIR=/run/user/UID systemctl --user start UNIT`), `podman start X` for a plain container. Service watch does this for you, with approval |
| `required container X ... does not exist` / `is not running` (critical) | a container in `check_containers_required` is missing or stopped | create or start it; fix its name in the setting |
| `container X (owner O) is running but its healthcheck reports unhealthy` (critical) | its own healthcheck fails | `podman inspect X` shows the last healthcheck output (`State.Health.Log`); the container's log |
| `container X (owner O) stopped with an error: it exited with code N, 2.5 hours ago` (warning) | a container that is not meant to run all the time stopped with an error in the last `check_containers_recent_hours` (24) hours | its log. Older crashes are not reported |
| `container X (owner O) has restarted N times - a restart loop` (warning) | more restarts than `check_containers_restart_warn` (3), counted by podman or by its systemd unit | its log shows why it keeps stopping |
| `container X (owner O) was killed by the out-of-memory killer` (warning) | the kernel killed it for memory | more memory on the host, or a higher memory limit on the container |
| `user U (uid N) has containers that should run (...) but linger is off` (warning) | the user's containers stop when they log out and do not start at boot | `loginctl enable-linger U` (as root) |
| `user U (uid N) has podman containers ... and N unit(s) that should start at boot, but they could not be checked: no /run/user/N` (warning) | the user has units that should start at boot (a Quadlet with `[Install]`, an enabled unit) but is not logged in and has no linger: none of their containers run now. A user with only container storage (an admin who once ran podman) is listed under `not checked:` and gets no finding | if they should run: `loginctl enable-linger U`. If not, leave them out: `podman_discover_ignore: ['U/.*']` |
| `the containers of O could not be read: podman ps failed ...` (warning) | podman (or systemctl) failed for that owner: a blind spot | run the `look:` command to see the error (storage problems, `podman system migrate` needed after an upgrade ...) |

**Settings** (roles `podman_discover` and `check_containers`; [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md)):

| Setting | Default | What it does |
|---|---|---|
| `podman_discover_enabled` | `true` | `false` = do not look for containers (the check says `skipped`) |
| `podman_discover_rootless` | `true` | also look at users' containers |
| `podman_discover_users` | `[]` | more users to look at, e.g. `[appsvc]` when the rules above miss one |
| `podman_discover_ignore` | `[]` | containers to leave out: regular expressions that must match the **whole** name or `OWNER/NAME`, e.g. `['test-.*', 'alice/.*', 'root/scratch']` |
| `check_containers_required` | `[]` | containers that must exist and run: a name (any owner) or `OWNER/NAME`, e.g. `[stigman-api, alice/web]` |
| `check_containers_recent_hours` | `24` | how recent a crash must be to be reported |
| `check_containers_restart_warn` | `3` | restarts above this = a restart loop |

**To verify it** on one host: run the Health check with `health_checks: containers` and a Limit of
that host; compare the table with `sudo podman ps -a` and, for a user,
`sudo runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/$(id -u alice) podman ps -a`. Service watch
uses the same way of finding containers ([SERVICE_WATCH_DEMO.md](SERVICE_WATCH_DEMO.md)).

## Troubleshoot (`playbooks/troubleshoot.yml`)

For "something is wrong on this host": pick the area and the job runs the commands an experienced
admin runs first. Each is printed under a line saying what it shows, and the matching health
checks run too. Read-only. Findings do not fail this job: you asked for a look, not a verdict.

| Area | Commands (what they show) | Checks |
|---|---|---|
| overview | uptime, failed units, disk, memory, recent errors, recent reboots | disk, mounts, services, performance, time, logging |
| disk | space and inodes, biggest directories on the fullest filesystem, deleted-but-open files, files over 1 GiB, journal size | disk, mounts |
| performance | load, top, vmstat, memory users, disk latency (iostat), load history (sar), OOM kills | performance |
| service (`ts_service`) | its status, why it stopped (result, exit code, restarts), its log, dependencies, unit file, listening ports | services (plus that unit) |
| network (`ts_target` host:port) | addresses, routes, DNS servers, name lookup, TCP connect, route taken, firewall, connection counts | network (plus that target) |
| selinux | mode, today's denials, audit2why, changed booleans, local file labels | selinux |
| fapolicyd | status, today's denials, rules, enforcing? | fapolicyd |
| login | sshd log, faillock lockouts, failed logins, SSSD domain status, authselect, clock offset | accounts |
| time | chrony tracking and sources, timedatectl, configured servers | time |
| logs | errors, top error senders, rsyslog and its queue, journal size | logging |
| mariadb (or mysql) | (the MariaDB / MySQL health check) | mariadb |

Add `use_act=true` and ACT reads everything collected and explains the root cause
([ADDING_ACT.md](ADDING_ACT.md)).

## Certificate report (`playbooks/cert_report.yml`)

The certs check on every host, and **one table**, soonest expiry first: days left, date, host,
subject, and where the certificate is. Hand it to whoever renews certificates. It fails the hosts
with a certificate inside `check_certs_warn_days`, so a workflow can open tickets.

## POA&M status (`playbooks/poam_status.yml`)

Reads `poam/poam.csv` (see [poam/README.md](../poam/README.md) for the columns and how to export
from eMASS):

| Finding | Means |
|---|---|
| `POA&M 1001 [CAT II]: ... - OVERDUE by 12 days` | open item past its scheduled completion date (critical) |
| `POA&M 1002 ...: due in 20 days` | due within `poam_due_soon_days` (30) |
| `... scheduled completion date "TBD" is missing or not a date` | fix the date in the list |
| `open CAT I finding V-123456 (...) on 2 asset(s) in collection Production has no POA&M item` | STIG Manager cross-check: an open CAT I/II finding that no POA&M item covers |

It also lists "maybe ready to close": items whose STIG findings are no longer open in STIG
Manager.

**The STIG Manager cross-check** is optional. It needs a Keycloak client that may use the
client-credentials grant, with the scope `stig-manager:collection:read`, and a grant in the
collections it reads. **Your realm owner creates that client**; nothing here changes the realm.
Then create the *STIG Manager API* credential (token URL
`https://<keycloak>/realms/<realm>/protocol/openid-connect/token`, client ID, secret), and set
`poam_stigman_api: https://<stigman>/api`.

## ServiceNow tickets (`playbooks/servicenow_tickets.yml`)

A workflow step after a check. For each finding at or above `servicenow_min_severity`:

- **no open ticket** for that problem on that host → it opens an incident: short description
  `[site-health] host: summary`; description with the host, check, finding, the `look:` command,
  and ACT's analysis if ACT ran; urgency/impact from the severity; assignment group, category
  and configuration item from the settings.
- **an open ticket exists** → it adds a work note `Still failing at ...`
  (`servicenow_note_repeats`).
- **a host that was targeted but never reported** → a ticket too (unreachable, login failed).
- **a problem that cleared** (its check ran again and passed) → a work note on its ticket, or it
  resolves it with `servicenow_resolve_cleared: true`.

Tickets are matched by a correlation ID (`site-health:<host>:<check>:<finding>`, hashed), never
by text, so the same problem always updates the same ticket. Without the ServiceNow API credential
it only prints what it would open. If no check results reach it at all (the check step died before
checking any host), it fails, so a broken schedule shows red instead of green.

## ServiceNow - test ticket (`playbooks/servicenow_test_ticket.yml`)

Proves the ServiceNow setup end to end, with the same calls and settings as the tickets step. Six
steps, each PASS, WARN or FAIL with what usually causes a failure:

| Step | What it does | FAIL usually means |
|---|---|---|
| 1 connection | logs in and reads one incident | certificate (CA), DNS, firewall or proxy; HTTP 401 = user or password; 403 = no `itil` role; 404 = a path after the host name in the Instance URL |
| 2 settings | `servicenow_assignment_group` and `servicenow_caller` exist | the name differs from ServiceNow's |
| 3 open | opens a low-priority incident: `[site-automation test] ... safe to close` | the account may not create incidents (`itil`), or ServiceNow refused a value (it says which) |
| 4 read back | reads it back: number, state, group, caller; prints a link | the account may not read it; WARN if the group did not stick |
| 5 work note | adds a work note | the account may not update incidents |
| 6 resolve | resolves it (`servicenow_test_resolve: false` leaves it open) | WARN only: your instance needs another resolved state or close code (`servicenow_resolved_state`, `servicenow_close_code`) |

As *Check* (a dry run) steps 1 and 2 run, and 3 to 6 are only printed. Set up, run and verify it:
[SERVICENOW_SETUP.md](SERVICENOW_SETUP.md).

## ServiceNow health (`playbooks/servicenow_health.yml`)

From the controller: does the instance answer its REST API, and how fast
(`servicenow_slow_seconds`)? Is every MID Server **Up** and **validated**? Hourly is a good
schedule. Alert with an AAP **notification** (email) on failure, not with a ticket: a broken
ServiceNow cannot take tickets. The MID Server **hosts** (the Linux service, its keystore) are
covered by the health check: see `inventories/example/group_vars/servicenow_mid_hosts.yml`.

## Patch hosts (`playbooks/patch_hosts.yml`)

Installs updates with dnf (`patch_security_only` for security fixes only; `patch_exclude` for
packages never updated here). Then:

1. **One host at a time** (`patch_serial`). The rollout **stops at the first host that fails**.
2. Refuses with less than `patch_min_free_mb` free in `/var`.
3. **No automatic restarts** unless `automatic_restarts: true` (the survey or the template's
   Variables). Without it, a host that needs a reboot is listed ("reboot it by hand, or run again
   with automatic_restarts: true"). With it, the job reboots only when `needs-restarting -r` says
   so (`patch_reboot: when_needed`), and **never** hosts in `patch_never_reboot_groups`
   (`aap_hosts`).
4. After the update (and reboot), **every service that was running before must be running
   again**. If one is not, the host fails and the rollout stops.
5. Never patches hosts in `no_patch` (vendor appliances), `aap`, `aap_hosts`, `sat`, `netapp` or
   `netapp_console_hosts`. The AAP host is **always** protected: even a list you write yourself
   (`patch_never_patch_groups`) cannot remove `aap` or `aap_hosts`. It also refuses a host that is
   the machine running the job, but only when the connection is `local` or the name is
   `localhost`; a normal SSH host that happens to be the controller is stopped by the group rule
   above, not by this one.

Run it as **Job type: Check** first: it lists what would be updated and changes nothing
([DRY_RUNS.md](DRY_RUNS.md)).

## Apply approved ACT fix (`playbooks/act_fix_approved.yml`)

The step after an approval in the *Fix with approval (ACT)* workflow. It runs **exactly** the
commands ACT proposed and a person approved, then re-runs the checks that found the problem. It
refuses to run on its own, and skips diagnose-only hosts (`aap_hosts`, `netapp_console_hosts`):
there a person applies the fix by hand. See [ADDING_ACT.md](ADDING_ACT.md).

## VMware jobs and reports (`playbooks/vm_*.yml`)

Restart, shut down, snapshot, notes, change VLAN; the secure boot, datastore, snapshot and
alarms reports; the snapshot cleanup; ACT's analysis of the alarms. They work through vCenter:
setup, every report's sections and troubleshooting are in [VMWARE.md](VMWARE.md).

## Windows hosts

Windows has its own playbooks, checks and troubleshooting areas (for example `updates` and
`security` in *Windows troubleshoot*). They are described, with their settings, in
[WINDOWS.md](WINDOWS.md). Settings for them are read the same way as any other:
[VARIABLES.md](VARIABLES.md).

## Not built yet (and why)

| Idea | Why not yet |
|---|---|
| SCAP scan → import into STIG Manager | Needs the same Keycloak client as the POA&M cross-check, plus a decision on SCC vs OpenSCAP content. STIG Manager's own importer (stigman-watcher) is the supported path |
| Certificate renewal workflow | Depends on how your PKI issues certificates. For STIG Manager, renewal already works: update the TLS credential, run *STIG Manager - deploy* |
| AAP self-health (job failure rate, execution environment and token age) | Needs an AAP API credential; AAP's own dashboards cover most of it today |
| Container runbooks (image updates with rollback, drift) | Deferred on purpose; *Service watch* covers container outages |
| MariaDB backups, security baseline, password rotation | Out of scope for now: health only, as asked |
