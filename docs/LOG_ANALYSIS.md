# Log analysis: point it at a group of servers, GenAI reads their logs

Two jobs read logs and have ACT (GenAI) explain what they show. Both are read-only.

| Job template | Playbook | What it reads |
|---|---|---|
| Log analysis | `playbooks/log_analysis.yml` | the hosts of the job's target or Limit: Linux (the journal, units, podman containers, text logs), Windows over WinRM (event logs, providers, event IDs, text logs), a vCenter appliance over SSH |
| VM - vCenter log analysis | `playbooks/vm_log_analysis.yml` | vCenter's own log (vpxd) and each ESXi host's (hostd, vmkernel, vpxa), through vCenter's API: no SSH, nothing to switch on at the hosts |

## What it does

1. **Read.** Each host's **warning, error and critical** lines of the window: the last
   `log_analysis_hours` (24), or a fixed window such as the outage, `log_analysis_since` ..
   `log_analysis_until`. The journal and the event logs say how bad a line is; a text log's line is
   read when it matches `log_analysis_match` (error, fail, fatal, denied, refused, timeout ...). At most
   `log_analysis_max_lines` (500) lines per log, the newest.
2. **Group, on the AAP side.** Each line becomes a pattern: the line with its numbers, times, IP
   addresses, IDs and quoted values taken out. The same pattern on five hosts is **one** row, "on 5
   hosts, 212 lines, first 07:02, last 07:58". Patterns are ranked: critical, then error, then
   warning; within a level, the most hosts and then the most lines first.
3. **Explain.** ACT gets the top `log_analysis_max_items` (30) patterns, with up to three example
   lines each, and answers for each: the **likely cause**, the **evidence**, the **fix**, and a
   **confidence** from 0 to 100. It is told to say when a pattern is harmless noise and to name the
   patterns that share a cause. ACT runs in **evidence mode**: on the AAP side, reading what the job
   collected, running **no command** on any host. Before anything leaves, ACT replaces the host names
   (and `log_analysis_names_extra`) with placeholders.
4. **Report.** One report, every run, in the same look as the other reports: printed in the job
   output, kept as the job artifact `log_analysis` (every pattern, ACT's answer merged in), **emailed**
   (`report_email_to`, and the "Also email the report to" box: [EMAIL_REPORTS.md](EMAIL_REPORTS.md)) and
   **published** on the report website (`report_web_dest`: [WEB_REPORTS.md](WEB_REPORTS.md)). It has:
   - the hosts **not read**, in red, with why (unreachable, login refused, a log it may not read);
   - your question, if you asked one, and ACT's "What to do first";
   - **Likely causes and fixes**: one row per pattern ACT analyzed, with the confidence coloured
     (green 80+, amber 50-79, red under 50);
   - **Patterns**, always - also without ACT: level, hosts, log, lines, first and last seen, an example;
   - the hosts read, with the logs each one gave;
   - **What was read**: the window, the profiles per host, how many lines the ignore list left out.

A run that finds nothing is not emailed unless `log_analysis_email_if_clean: true` (the website gets
every run). The job is **green** when it reports; **red** when ACT was wanted and could not run (no
key, no network: the report still lists the patterns and says why), when no host at all could be
read, and - with `log_analysis_fail: true`, for a workflow - when a critical or error pattern was found.
A host that cannot be reached is listed, and does not fail the job.

## What it reads, per kind of host

`log_analysis_source` says what a host is: `auto` (Windows when its connection is WinRM or PSRP, else
Linux), `linux`, `windows` or `vcsa`. Set it per group in the inventory.

| Kind | Read | How |
|---|---|---|
| Linux | the journal at `log_analysis_priority` (`err`) or worse; the units in `log_analysis_units` and the syslog identifiers in `log_analysis_identifiers` at `log_analysis_units_priority` (`warning`); the containers in `log_analysis_containers` (`podman logs --since`); the text logs in `log_analysis_files` (+ `_extra`, + the profiles') that changed in the window | journalctl, podman, find and grep, as root (`log_analysis_become`) - read-only. A journal line two reads return is counted once |
| Windows | the event logs in `log_analysis_win_logs` (+ `_extra`): critical, error and (`log_analysis_win_level: 3`) warning events; the providers in `log_analysis_win_providers` at every level up to information; the event IDs in `log_analysis_win_event_ids` whatever their level; the text logs in `log_analysis_win_files` (+ `_extra`) | `files/log_collect.ps1` over WinRM - no collection needed. PowerShell 5.1 and 7, also in Constrained Language Mode |
| vCenter appliance (`vcsa`) | as Linux, plus the `vcsa` profile: vpxd, vpxd-svcs, vmon, SSO (STS), envoy, rhttpproxy, vsphere-ui, applmgmt, vpostgres, vmdird, certificate management, EAM, content library | over SSH as root. **The appliance's login shell must be bash** for Ansible: on the appliance, `chsh -s /bin/bash root`. Its Machine credential: root, or an account that may sudo |
| vCenter and ESXi through the API | vCenter's `log_analysis_vcenter_logs` (`vpxd`); each host's `log_analysis_esxi_logs` (`hostd`, `vmkernel`, `vpxa`; also e.g. `fdm`, `vobd`) - the last `log_analysis_read_lines` (20000) lines of each, then the window; a line ESXi or vCenter marks warning, error or critical, or one that matches `log_analysis_match` | `playbooks/vm_log_analysis.yml`, vCenter's DiagnosticManager (what the vSphere Client's "Export System Logs" reads). The **VMware vCenter** credential's account needs **Global > Diagnostics** besides read-only. A host not connected to vCenter is listed as not read |

## The settings

Every setting can be set for all hosts (`all.yml`), per group or host in the inventory, on the job
template, or as a **survey** answer. A list may be typed as `a, b` or one per line.

| Setting | Default | What |
|---|---|---|
| `log_analysis_hours` | `24` | the window: the last this-many hours |
| `log_analysis_since`, `log_analysis_until` | empty | a fixed window, UTC, e.g. `2026-10-23 08:00` and `2026-10-23 10:30` ("what happened during the outage"). Since alone = until now |
| `log_analysis_question` | empty | what you want to know, in your words: ACT answers it first ("users report slow logins since 09:00"). A good survey box |
| `log_analysis_profiles` | `[]` | one word switches on a product's logs (below) |
| `log_analysis_profiles_extra` | `{}` | your own profiles, or a shipped one changed: `{stigman: {files: [/opt/stigman/logs/*.log], units: [stigman-api]}}` |
| `log_analysis_source` | `auto` | `auto`, `linux`, `windows`, `vcsa` |
| `log_analysis_priority` | `err` | the journal's lines this bad or worse (`crit`, `err`, `warning` ...) |
| `log_analysis_journal` | `true` | `false` = no general journal read (units, containers and text logs only) |
| `log_analysis_units`, `log_analysis_identifiers` | `[]` | units (`sssd`, `dirsrv@*`) and syslog identifiers (`sudo`) read at `log_analysis_units_priority` (`warning`) - lower than the rest |
| `log_analysis_containers` | `[]` | podman containers: `name` for root's, `user/name` for a user's (rootless) |
| `log_analysis_files`, `log_analysis_files_extra` | `[]` | Linux text logs (globs). `_extra` adds to the base list and to the profiles' - it never replaces them |
| `log_analysis_win_logs`, `log_analysis_win_logs_extra` | `[System, Application]`, `[]` | event log channels, e.g. `Microsoft-Windows-TaskScheduler/Operational`, `Directory Service`, `DNS Server` |
| `log_analysis_win_level` | `3` | 1 critical, 2 + error, 3 + warning |
| `log_analysis_win_providers` | `[]` | providers read at every level up to information (`Service Control Manager`); `log_analysis_win_providers_only: true` = only these |
| `log_analysis_win_event_ids` | `[]` | IDs always read: `"System:7031"`, or a number for every log read plus Security (`4625`, `4740`) |
| `log_analysis_win_files`, `log_analysis_win_files_extra` | `[]` | Windows text logs (wildcards), e.g. `'D:\Apps\*\logs\*.log'` |
| `log_analysis_match`, `log_analysis_match_extra` | error, fail, fatal ... / `[]` | the words a text log's line (or a container's) is read for; `_extra` adds words (`SEVERE`) |
| `log_analysis_ignore`, `log_analysis_ignore_extra` | two harmless Windows events / `[]` | lines left out before they are grouped (regular expressions, matched against "program line"): known noise. `_extra` adds yours |
| `log_analysis_names_extra` | `[]` | more names ACT replaces with placeholders (host names always are) |
| `log_analysis_act` | `true` | `false` = the patterns only, no ACT, no key needed |
| `log_analysis_max_items` | `30` | ACT gets at most this many patterns, the most severe first; the rest are in the patterns table |
| `log_analysis_max_lines`, `log_analysis_max_line_chars` | `500`, `400` | per log: the newest lines kept; a longer line is cut |
| `log_analysis_max_chars` | `60000` | the evidence ACT reads is cut here |
| `log_analysis_min_count` | `1` | a pattern seen fewer times is left out |
| `log_analysis_email_if_clean` | `false` | `true` = email also when nothing was found |
| `log_analysis_fail` | `false` | `true` = the job is red when a critical or error pattern was found |
| `log_analysis_esxi_hosts` | `[]` | `vm_log_analysis.yml`: only these hosts (`esx0*`) |
| `log_analysis_vcenter_logs`, `log_analysis_esxi_logs`, `log_analysis_read_lines` | `[vpxd]`, `[hostd, vmkernel, vpxa]`, `20000` | `vm_log_analysis.yml`: which logs, and how far back each is read |

ACT's own settings are the usual ones ([ADDING_ACT.md](ADDING_ACT.md)): `site_act_provider` (GenAI.mil by
default), `site_act_models`, `site_act_env` (a proxy), `site_act_ca`; `log_analysis_act_model_timeout`
(600 s) for one answer.

## Profiles

A profile is a product's standard log locations and units, switched on with one word. They ship in
`roles/log_analysis/vars/main.yml`; yours go in `log_analysis_profiles_extra` (an entry of the same
name replaces the shipped one). What your site moved elsewhere goes in the `_extra` lists.

| Profile | Reads |
|---|---|
| `vcsa` | the vCenter appliance's logs (on by itself with `log_analysis_source: vcsa`) |
| `mid_server` | a ServiceNow MID Server on Windows: `agent0.log*` and `wrapper.log*` under `C:\ServiceNow\*\agent\logs` and `D:\...`; its service stopping or crashing (System 7023, 7024, 7031, 7034); lines with SEVERE |
| `iis` | WAS / W3SVC / worker process events; the HTTP.sys error logs (`HTTPERR`) |
| `sql_server` | the MSSQLSERVER and SQL Agent events; `ERRORLOG*`; severity 16-25, deadlocks, failed logins |
| `nginx`, `httpd` | their error logs and units |
| `mariadb` | MariaDB and MySQL error logs, `mariadb` and `mysqld` units |
| `podman` | the podman units and the `podman` / `conmon` identifiers |
| `sssd` | the SSSD logs and unit |
| `idm` | an IdM (FreeIPA) server: Directory Server errors, the KDC, kadmin, httpd, named, custodia, the CA |
| `auditd` | `audit.log`'s failed results (`res=failed`, `success=no`) and the unit |
| `aap` | an AAP node installed from RPMs: `/var/log/tower/*.log`, the controller, receptor, gateway, nginx and redis units. A containerized AAP: name its containers in `log_analysis_containers` (`user/automation-controller-task` ...) |

The **MID Servers run on Windows**: put them in a group of the Windows inventory with

```yaml
log_analysis_profiles: [mid_server]
# installed elsewhere than C:\ServiceNow or D:\ServiceNow:
log_analysis_win_files_extra: ['E:\MID\*\agent\logs\agent0.log*']
```

## Set it up in AAP

1. **The key.** The **ACT model key** credential your other ACT jobs use (or create it:
   `aap/credential_types/act_model_key.yml`). Not needed with `log_analysis_act: false`.
2. **Log analysis** (`playbooks/log_analysis.yml`) - one template per inventory:
   - Linux servers: the Linux inventory and its **Machine** credential (an account that may sudo).
   - Windows servers: the Windows inventory and its **Machine (Windows)** credential (WinRM).
   - The vCenter appliance: a host in a group with `log_analysis_source: vcsa`, its Machine
     credential root (or a sudo account), the appliance's shell bash (above).
   - Credentials: the Machine credential **and** ACT model key. **Prompt on launch** for the
     **Limit** (hosts only - since 0.32.0 a Limit needs no `localhost`; one with it works too), and a
     **survey** with `target` (the group), `log_analysis_question` (text, not
     required), `log_analysis_since` / `log_analysis_until` (text, not required) and, if you like,
     `log_analysis_profiles` (multiple choice).
   - Variables: `report_email_to: ops@yoursite.mil` (or empty, with `report_web_dest` only).
3. **VM - vCenter log analysis** (`playbooks/vm_log_analysis.yml`): inventory, project and execution
   environment as the other VM templates; credentials **VMware vCenter** (Global > Diagnostics) and
   **ACT model key**; no Limit. Several vCenters: `vmware_vcenters`, as the other VM jobs.
4. **First run as a dry run:** Job type **Check**. Everything is read and reported; ACT is not called.

## Sizes and the GenAI quota

ACT is called **once per job**, whatever the number of hosts: the grouping does the shrinking (20 hosts
x 500 lines become a few dozen patterns), and the evidence is capped at `log_analysis_max_chars`
(60000 characters, roughly 15000 tokens) - well inside GenAI.mil's default quota of 200,000 tokens a
minute per key. The lines themselves travel from each host to the AAP node: at most
`log_analysis_max_lines` per log, `log_analysis_max_line_chars` each. Raise `log_analysis_max_items`
for more patterns explained, or lower it (and set `log_analysis_min_count: 3`) when a noisy group
crowds the list; quiet known noise for good with `log_analysis_ignore_extra`.
