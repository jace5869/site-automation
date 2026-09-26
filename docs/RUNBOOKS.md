# The runbooks: what each one checks, and what to do about it

For each runbook: what it answers, its settings (defaults in `roles/<role>/defaults/main.yml`,
change them in the inventory), and each finding with what usually fixes it. The `look:` command
printed with a finding is always the first thing to run.

## Health check (`playbooks/health_check.yml`)

Runs the checks you pick on each host: a list of names, or the shortcuts:

- `daily` = disk, mounts, services, performance, time, network, logging, mariadb
- `weekly` = selinux, fapolicyd, auditd, accounts, certs, patching
- `all` = both

A host with a finding at a severity in `site_fail_on` (default: critical and warning) fails. That
is what a workflow reacts to. A check that itself breaks becomes a warning finding (`the X check
could not run: ...`) and the other checks still run.

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

### mariadb (health only)

Runs on hosts in the `mariadb_hosts` group. It runs status queries only: no data is read and
nothing is changed. For MariaDB in a container (the ServiceNow database), set
`check_mariadb_container: <name>`; the client inside the container is used.

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
| `only 91% of reads come from memory` | `innodb_buffer_pool_size` may be too small | the DBA |
| `3 [ERROR] line(s) in the MariaDB log` | errors in its log in the last day (latest shown) | read them |
| `the MariaDB data directory is on a filesystem 93% full` | MariaDB stops writing when it fills | disk space |

**Logging in.** Without a credential, the check logs in as root through the local socket. That
is MariaDB's default for a host install on RHEL (no password needed with sudo). **MariaDB in a
container usually gives root a password** (the official images do), and the check then reports
`Access denied ... attach the "MariaDB monitor" credential`. For containers, or whenever you prefer
not to use root, ask the DBA to create a monitoring account with **no data access**, inside that
database with host `localhost`, and attach the *MariaDB monitor* credential to the Health check
template:

```sql
CREATE USER 'aap_monitor'@'localhost' IDENTIFIED BY '<password>';
GRANT PROCESS, SLAVE MONITOR ON *.* TO 'aap_monitor'@'localhost';   -- MariaDB 10.5.9+
-- older MariaDB: GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'aap_monitor'@'localhost';
```

`PROCESS` shows other sessions (for long queries); `SLAVE MONITOR` shows replication status. The
password reaches the client only through its environment, never its command line.

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
| mariadb | (the MariaDB health check) | mariadb |

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
3. Reboots only if `needs-restarting -r` says so (`patch_reboot: when_needed`), and **never**
   hosts in `patch_never_reboot_groups` (`aap_hosts`). Those get a "reboot by hand" message.
4. After the update (and reboot), **every service that was running before must be running
   again**. If one is not, the host fails and the rollout stops.
5. Skips hosts in `no_patch` (vendor appliances, and AAP itself in the example inventory), and
   refuses to patch the machine running the job.

Run it as **Job type: Check** first: it lists what would be updated and changes nothing.

## Apply approved ACT fix (`playbooks/act_fix_approved.yml`)

The step after an approval in the *Fix with approval (ACT)* workflow. It runs **exactly** the
commands ACT proposed and a person approved, then re-runs the checks that found the problem. It
refuses to run on its own, and skips diagnose-only hosts (`aap_hosts`, `netapp_console_hosts`):
there a person applies the fix by hand. See [ADDING_ACT.md](ADDING_ACT.md).

## Not built yet (and why)

| Idea | Why not yet |
|---|---|
| SCAP scan → import into STIG Manager | Needs the same Keycloak client as the POA&M cross-check, plus a decision on SCC vs OpenSCAP content. STIG Manager's own importer (stigman-watcher) is the supported path |
| Certificate renewal workflow | Depends on how your PKI issues certificates. For STIG Manager, renewal already works: update the TLS credential, run *STIG Manager - deploy* |
| AAP self-health (job failure rate, execution environment and token age) | Needs an AAP API credential; AAP's own dashboards cover most of it today |
| Container runbooks (image updates with rollback, drift) | Deferred on purpose; *Service watch* covers container outages |
| MariaDB backups, security baseline, password rotation | Out of scope for now: health only, as asked |
