# Changelog

## 0.5.0 — 2026-09-30

Safety, settings and documentation release, from a review of the code and the guides.

**Upgrading from 0.4.1 - read this first**
- **Your `playbooks/group_vars/all.yml` keeps its old settings.** The update scripts never change
  your settings files, so the 0.4.1 lines are still real (uncommented) lines in your copy:
  `site_act_diagnose_only_groups`, `patch_never_patch_groups`, `patch_never_reboot_groups`,
  `site_act_provider` and the `site_act_models:` block (with its `genai:` and `asksage:` lines).
  They still win over the same settings in AAP's inventory and group Variables boxes, and they
  replace the new built-in lists (your old `patch_never_*` lists lack `netapp_console_hosts`). Put
  `# ` in front of those lines (compare with the release's `playbooks/group_vars/all.yml`) unless
  you want exactly those values. Each job now prints `NOTE - settings in this project's files win
  ...` with what your files still set. `docs/VARIABLES.md` explains the order.
- **The database check runs on more hosts.** Until now only the group `mariadb_hosts`; now also
  `mysql_hosts`, `database_hosts`, `mariadb`, `mysql` and any host that names a container. Hosts in
  those groups get the check in every `daily` Health check: without the *MariaDB monitor*
  credential on the template, a containerized database reports `mariadb:connect` (Access denied),
  which fails the host and, in a workflow, opens a ticket. Attach the credential, or remove the
  host from those groups. A monitoring account made with the 0.4.1 grant (`SLAVE MONITOR`, the
  same privilege as `REPLICA MONITOR`) needs no change.
- **Survey choices are not updated for you.** In AAP, add `mysql` and `database` to the Health
  check survey's `health_checks` choices and `mysql` to Troubleshoot's `ts_area`, and create the
  *Database health - MariaDB / MySQL* template (`docs/SETUP_AAP.md`) if you want it. Workflow 8
  now uses that template; the old Health check version keeps working.
- **A check that could not run now fails the host** (`<check>:check-error`), also when
  `site_fail_on` is `[critical]`. Expect a red host where a check used to break quietly.
- **ACT 0.6.21** (see below): `--auto` asks for danger-tier commands; the key is only sent to an
  `https://` provider URL. If your `site_act_url` / `site_act_urls` use `http://`, change them or
  set `site_act_env: {ACT_ALLOW_HTTP: "1"}` (Linux; ACT-Windows uses `ACT_ALLOW_HTTP_KEY`).
- Credential types: no input or injector changed; nothing to re-paste in AAP.

**Security fixes - secrets no longer on a command line**
- **ACT API key** (vendored ACT's `act_triage` role, used by every ACT-in-a-job run): the key was
  handed to ACT through Ansible's `environment:`, which Ansible puts on the `sudo /bin/sh -c ...`
  command line. So the key was visible in `ps` and **written to the sudo log (journal /
  `/var/log/secure`) of every host ACT ran on**, `no_log` or not. It now goes on the task's stdin.
  If you ran ACT from AAP with 0.2.0 - 0.4.1, **rotate the ACT key** and treat the managed hosts'
  sudo logs from that period as containing it.
- **MariaDB / MySQL monitor password**: same problem (`MYSQL_PWD=...` on the sudo command line and
  in the sudo log of every database host, and in a fact shown at `-v`). It now goes on stdin; no
  fact holds it. Rotate the monitor account's password if the check ran with 0.3.x - 0.4.1.
- **Java keystore passwords** (`check_certs_keystores` with `password_env`): same fix.
- `docs/SECRETS.md` rule 3 now says it plainly: Ansible's `environment:` counts as a command line.

**Safety**
- **The AAP host can never be patched, restarted or fixed by ACT**, whatever list you write. The
  groups `aap` and `aap_hosts` are always protected (`roles/patch/vars`, `roles/site_act/vars`,
  and written into the skip and reboot conditions themselves, so not even an extra variable
  removes them). Your own `patch_never_*` / `site_act_diagnose_only_groups`
  lists replace the shipped ones, but `aap` and `aap_hosts` stay in. Windows patching follows the
  same lists.
- **An apply-time guard** checks the exact command an approver approved, before it runs, in
  *Apply approved ACT fix* and in *Service watch* (apply), dry runs included. It refuses what can
  never be approved (reboot, shutdown, `kexec`, `mkfs`, account changes, recursive deletes of
  system folders ... also as `/sbin/reboot` or inside `bash -c '...'` / `$( )`)
  and *Apply approved ACT fix* also logs what it ran to `/var/log/site-automation-fix.log` on the host.
- A check that could not run (`<check>:check-error`) now fails the host also when `site_fail_on`
  is `[critical]`, so it is not mistaken for a healthy one.
- *Apply approved ACT fix* and Windows patching fell back to shorter protected-group lists than
  the role defaults when `all.yml` did not set them; they now use the same full lists
  (`tests/test_protection.py` checks they stay equal).
- A `!vault` value in any settings file no longer makes every check playbook fail.

**Settings**
- `playbooks/group_vars/all.yml` is now all commented examples; the role defaults are the single
  source. A job prints a notice when a project settings file overrides AAP's Variables box
  (`site_settings_notice: false` silences it).
- The update scripts read `.site-local` more strictly (folders without a slash, `..` refused),
  print a summary count, list a settings file the release adds as `YOURS` (added once), and list
  under "NEW SETTINGS" the settings the release's `all.yml` mentions and yours does not.

**MariaDB and MySQL** (`roles/check_mariadb`)
- The database check works with **MariaDB and MySQL 5.7 / 8.0 / 8.4** (and Percona), on the host or in
  **podman containers**: one, several, found by image name, and rootless
  (`check_mariadb_container_user`). The engine is detected from the server's version, and the
  replication statement is chosen to match (`SHOW ALL SLAVES STATUS`, `SHOW REPLICA STATUS` or
  `SHOW SLAVE STATUS`). Findings name the container when there are several.
- New playbook **`playbooks/database_health.yml`** ("Database health - MariaDB / MySQL"): read-only,
  targets `mariadb_hosts`, `mysql_hosts` and `database_hosts` by default, ends with the report.
  The Health check survey also accepts `mysql` and `database` (same check as `mariadb`), and
  Troubleshoot accepts `mysql`. Groups `mysql_hosts`, `database_hosts` and `mysql` enable the check
  the same way `mariadb_hosts` does. New examples: `playbooks/group_vars/mysql.yml`,
  `inventories/example/group_vars/mysql_hosts.yml`.
- New findings: `mariadb:replication-query`, `mariadb:group`, `mariadb:group-size`,
  `mariadb:group-query` (Group Replication); new setting `check_mariadb_group_size` (0 = off).
  The finding hints give the right grant per engine: MySQL `PROCESS, REPLICATION CLIENT`;
  MariaDB 10.5.9 and newer `PROCESS, REPLICA MONITOR` (`REPLICATION CLIENT` is not enough).
- Tested against real rootless podman containers: MySQL 8.0, MySQL 8.4 and MariaDB 11.8.
  Group Replication and MySQL 5.7 were tested only against a fake `podman`
  (`bash tests/mariadb/run_mariadb_test.sh`). `docs/MARIADB.md` says so plainly.

**ACT**
- **ACT 0.6.21** vendored for Linux and Windows (`vendor/act`, `vendor/act-windows`): a hardening
  release. `--auto` / `-Auto` now always asks for danger-tier commands, and unattended
  (`--non-interactive`) runs refuse them; no `--allow` / `site_act_allow` pattern approves the
  danger tier, on Linux or Windows (`docs/APPROVED_COMMANDS.md`). Closed classifier holes
  (`sed -i`, `sed s///e`, awk `system()`, `git --output`, commands hidden after a `#` comment,
  quoted Windows verbs such as `reg 'delete'`) without gating any read-only command: a
  before/after comparison of ~970 Linux and ~475 Windows commands shows no read newly gated.
  The API key is sent only over `https://` and never through a redirect, and (see Security
  fixes) never on a command line. The start-up banner no longer names an organization
  (`ACT_BANNER_ORG` adds one). Windows reads large command output much faster.
- `scripts/update-act-windows.sh` / `.ps1`, `vendor/CHECKSUMS` and `tests/check_vendor.sh`:
  refresh and verify the vendored ACT for Linux and Windows together.

**Documentation** (also in the PDFs)
- New: `docs/APPROVED_COMMANDS.md` (how to pre-approve commands with `site_act_allow`, what can
  never be approved, how Windows differs), `docs/VARIABLES.md` (what you can set, where, and who
  wins, checked by running Ansible), `docs/VARIABLES_REFERENCE.md` (every setting and default,
  generated by `scripts/gen_variable_reference.py`; CI fails when it is out of date),
  `docs/MARIADB.md` (the MariaDB / MySQL check step by step, host or container).
- `docs/ADDING_ACT.md`: both endpoint formats and the `:probe` command, and how to carry its
  result into the jobs. Corrected: ACT provider settings are commented examples, not already set.
- Fixed from the documentation review: settings-file names and the group they
  apply to, WinRM certificate validation (validate first, `ignore` only for a lab test),
  secrets table (WinRM, the ACT key on managed hosts, rotation of ServiceNow, MariaDB, WinRM),
  ServiceNow sign-in (basic authentication only), the workflow tables, and a caveat that the
  approval hand-over has not been run end to end in a live AAP.
- The PDF build scripts now live in `docs/pdf/`.

## 0.4.1 — 2026-09-29

- **ServiceNow - test ticket** (`playbooks/servicenow_test_ticket.yml`): proves the ServiceNow
  setup before the tickets step goes live. It logs in, checks that the assignment group and the
  caller exist, opens a low-priority test incident (`[site-automation test] ... safe to close`),
  reads it back (with a link), adds a work note and resolves it. It uses the same API calls and
  settings as the tickets step. Each step prints PASS, WARN or FAIL, and a failure says what
  usually causes it (certificate, DNS, firewall or proxy, HTTP 401 / 403 / 404, the `itil` role, a
  group name that does not match). As *Check* (a dry run) it only logs in and reads.
  `servicenow_test_resolve: false` leaves the ticket open to look at.
- **`docs/SERVICENOW_SETUP.md`** (and a chapter in the setup PDF): ServiceNow step by step. It
  covers the API account to ask for (with a request you can copy), the instance URL and every API
  call the playbooks make, and the network path from the AAP execution nodes (firewall, proxy,
  DoD CA). Then the credential, your settings, the test ticket, how to check the ticket in
  ServiceNow, and a table of what each failure means.
- **`servicenow_ca_path`**: a CA bundle (PEM) for the instance's certificate, for every ServiceNow
  call (tickets, health, test ticket), when the execution environment does not trust its CA.
- **Workflows**: `docs/WORKFLOWS_AND_SCHEDULES.md` now explains how ACT fits a workflow. There is
  no ACT box: ACT runs inside the check job (`use_act`, `site_act_level`). "On fail" is the check
  job's own result (findings, `NEEDS APPROVAL`, a host it could not check), and `site_act` goes
  into a playbook, never into the workflow.
- **ACT 0.6.19** (vendored: `vendor/act`, `vendor/act-windows`), for both Linux and Windows:
  - **Both endpoint formats.** ACT now speaks the OpenAI one (`.../v1/chat/completions`) and
    the Anthropic Messages API (`.../v1/messages`). A model the gateway refuses on one is tried on
    the other, and ACT remembers which one works.
  - **Every HTTP 400 shows the server's reason.** It no longer prints only `Response status code
    does not indicate success`. A refusal that names a field (`temperature`, `max_tokens`,
    `tool_choice` ...) drops just that field.
  - **`:probe`** tests a model on both endpoints.
  - **In AAP:** `site_act_env` can pass `ACT_API_FORMAT` or `GENAI_ANTHROPIC_URL`
    (`docs/ADDING_ACT.md`).
- CI runs the test ticket against a fake ServiceNow (`tests/servicenow/`), with the current
  ansible-core and 2.16: a good account, a wrong password, a read-only account, an unknown group,
  a dry run, and an instance that refuses to resolve.

## 0.4.0 — 2026-09-29

- **No automatic restarts unless you say so** (`automatic_restarts: true`, default false), for
  Linux and Windows patching alike. **This changes Linux patching**: until now `patch_hosts.yml`
  rebooted hosts that needed it (`patch_reboot: when_needed`); now it installs the updates and
  lists the hosts that still need a reboot, unless `automatic_restarts` is true. The patch
  templates' survey question is now `automatic_restarts` (`no` / `yes`).
- **Dry runs everywhere** (Job type *Check*), explained in `docs/DRY_RUNS.md` (and a chapter in the
  setup PDF): what each template does in one, the five places to set it, examples. ServiceNow
  tickets now does a real dry run: it reads what is open and prints what it would open, note and
  resolve, sending nothing. POA&M status and ServiceNow health read as usual in a dry run. Patch
  hosts says "DRY RUN ... would make N change(s): <packages>". STIG Manager - deploy now works as a
  dry run (it reads what it needs, lists what would change, verifies nothing).
- **Workflows**: `docs/WORKFLOWS_AND_SCHEDULES.md` now shows how to build any workflow, click by
  click, and 17 ready-made ones for Linux, Windows and both: daily health, weekly compliance,
  patch with checks, patch with a dry run first, fix with approval (ACT), security posture,
  certificate watch, MariaDB watch, investigate a host, STIG Manager deploy with checks, service
  watch, the Windows ones, and "everything, every morning".

- **Windows servers.** Health checks, troubleshooting and a certificate report for Windows, over
  WinRM, the same way as for Linux: the same findings, report, ServiceNow tickets, POA&M and
  workflows. Each check is a PowerShell script (`roles/win_check_<name>/files`) run with the
  built-in `script` module, so no Ansible collection is needed (the execution environment needs
  `pywinrm`). The scripts run in Windows PowerShell 5.1 and PowerShell 7, and are written and
  tested for Constrained Language Mode too (whether Ansible itself runs on a WDAC / AppLocker
  server depends on the ansible-core version and the policy; the connection test shows it).
  - `playbooks/win_health_check.yml`: disk, services, performance, time, network (including the
    domain trust), eventlog (daily); security, audit, accounts, certs, patching (weekly). One
    Application event log entry per finding.
  - `playbooks/win_troubleshoot.yml`: overview, disk, performance, service, network, login, time,
    logs, updates, security.
  - `playbooks/win_cert_report.yml`: every certificate in the machine stores, with where it is used
    (https bindings, the WinRM listener AAP connects through, Remote Desktop).
  - `playbooks/win_connection_test.yml`: checks the execution environment (pywinrm, NTLM, Kerberos)
    and each server (PowerShell, language mode, local administrator).
- **Windows patching through Software Center** (`playbooks/win_patch.yml`, `roles/win_patch`):
  asks the ConfigMgr client to install every update deployed to the server (like "Install all"),
  waits and reports each update's state, restarts only with `automatic_restarts: true` and only
  when needed (shutdown.exe; AAP watches the WinRM port and checks the boot time changed), then
  proves every automatic service that ran before runs again. A second round after a restart
  catches updates the first one revealed. One server at a time, stops at the first failure; stops plainly outside a ConfigMgr
  maintenance window; Check mode lists and changes nothing. No collection needed.
- **ACT for Windows on the servers** (`playbooks/win_act_install.yml`, `roles/win_act`,
  `vendor/act-windows` 0.6.18): installed in `C:\ProgramData\act`, taken over and locked down to
  Administrators and SYSTEM (ordinary users may create files there), SHA256-checked against the
  repository and unblocked (no "downloaded from the internet" mark) every run;
  `win_act_state: absent` removes it.
- New filter `site_result`: the result line of a Windows script.
- **Linux and Windows do not mix**: the Linux playbooks skip Windows hosts and the Windows ones
  skip Linux hosts (`roles/site_findings/tasks/os_guard.yml`); skipped hosts get no "did not
  report" ticket.
- `docs/WINDOWS.md` (and a chapter in the setup PDF); `inventories/example-windows/`.
- Tests: the Windows checks with made-up server data (every finding path, normal and
  constrained), on Linux and on Windows under PowerShell 5.1 and 7; each check for real on a
  Windows runner; Ansible to Windows over WinRM end to end; and simulated patch runs (a stand-in
  ConfigMgr client, restart, second round, maintenance window, dry run, no client).

## 0.3.5 — 2026-09-28

- **Apply approved ACT fix runs the approved commands itself**, like service watch since 0.3.4:
  exactly the commands in the NEEDS APPROVAL line, in order, stopping at the first that fails,
  then the same checks again. No model and no key in the apply step (a revoked key or a vague
  model answer can no longer break it); the template needs only `Linux ssh (sudo)`.
- **Dry run of the whole approve-and-fix path**: run either apply step as *Check* (tick Prompt on
  launch for Job type, set the workflow node to Check). It prints `DRY RUN on <host>: approved,
  and would run: ...` and changes nothing.
- Docs: "Rehearse it" (WORKFLOWS_AND_SCHEDULES, SERVICE_WATCH_DEMO), ADDING_ACT and SETUP_AAP
  (apply templates need no ACT key); PDFs rebuilt.

## 0.3.4 — 2026-09-28

- **Service watch knows how each container is run.** For every watched container it finds the
  systemd unit that runs it: the container's `PODMAN_SYSTEMD_UNIT` label, a Quadlet `.container`
  file (`ContainerName=`, or the default name `systemd-NAME`), or a unit `NAME.service` /
  `container-NAME.service` whose ExecStart runs podman (so a host `nginx.service` that is not the
  container is ignored). `unit:` still overrides. A container a unit runs is started with
  `systemctl start UNIT` (restart when the unit is active, `reset-failed` first after a start
  limit), never `podman start`; a stopped Quadlet unit's missing container is reported as
  "not running: unit X is inactive", not as a container that must be recreated. Containers may be
  listed by service name (`stigman`, or `stigman.service`).
- **The fix to approve is made right**: ACT's `podman start/restart/run` of a unit-run container
  becomes the `systemctl` command; down containers ACT's fix does not cover get the standard fix;
  ACT proposing nothing (or not running: no key, no network) proposes the standard fix, labelled as
  such; commands in dependency order. Handed to the apply job as `service_watch_fix` (set_stats).
- **The apply job runs exactly the approved commands itself**, in order, stopping at the first that
  fails, then re-checks - no model, no key, no rewording between approval and fix. It no longer
  needs the ACT credential. Results from an older check job (`act_triage`) still work.
- **Self-heal**: still down after ACT, the playbook runs the standard fix itself and re-checks.
- ACT is told how each container is run and the only right way to start it, and to read
  `systemctl status` / `journalctl -u` for unit-run containers.
- `playbooks/group_vars/stigman.yml` (shipped copy): the STIG Manager stack's five services, and no
  longer says `target: stigman` is needed. Your copy is yours: edit it the same way.
- Docs: SERVICE_WATCH_DEMO updated; PDFs rebuilt.

## 0.3.3 — 2026-09-28

- **No `target` needed for the usual runs.** With an empty Variables box, Health check,
  Troubleshoot, Certificate report and Apply approved ACT fix run on **every host** of the
  template's inventory (the built-in group `all`, was `rhel_all`), and Service watch, its apply
  step and STIG Manager deploy on the group **`stigman`** (was `stigman_hosts`). Narrow a run with
  the Limit; `target:` in a template's Variables still overrides. Patch hosts is unchanged: it only
  patches the group `patch_hosts`, or the `target` you set, never everything by default.
- The example inventory's STIG Manager group is now `stigman` too.
- Docs: where the ACT provider and model go (the settings file `all.yml` beats the inventory's
  Variables box; a group's box or file, or a template's Variables, override it); `target` and
  Limit patterns (`a:b`, `a:&b`, `a:!b`); "no hosts matched" explained; PDFs rebuilt.

## 0.3.2 — 2026-09-28

- **Your settings files**, `playbooks/group_vars/`: one per AAP group of the site inventory (`all`,
  `stigman`, `mariadb`, `aap`, `sat`, `idm`, `logstash`, `netapp`, `rhel8_all`). Known values are
  set (safety lists, ACT provider and models, certificates, services); unknown ones are
  commented-out `CHANGE-ME` placeholders, so nothing fires until they are filled in. Ansible reads
  them on every run; the hosts and groups stay in AAP. A value here wins over the same value in an
  AAP group's Variables box.
- **The update scripts treat them as yours**: a settings file you do not have yet is added once
  (`YOURS` in the preview), one you have is never changed.
- **ACT model per provider**: `site_act_models` (and `site_act_urls`), so the model follows the
  provider you pick - defaults `genai: gemini-3.8-flash`, `asksage: gpt-5.6-sol-gov`.
  `site_act_model` still overrides for one run.
- Docs: USING_YOUR_AAP_INVENTORY starts with the settings files; HOW_IT_FITS_TOGETHER and
  ADDING_ACT updated; PDFs rebuilt.

## 0.3.1 — 2026-09-27

- **ACT provider in one setting**: `site_act_provider` (`genai` default, `asksage`, `genai-beta`),
  with `site_act_model`, `site_act_url` and `site_act_ca`, turned into ACT's environment for every
  runbook that uses ACT: health check, troubleshoot, apply approved fix and service watch. A
  missing key for the chosen provider is reported plainly and ACT is skipped; the findings are
  still reported. New credential type *ACT GenAI beta key*.
- **docs/HOW_IT_FITS_TOGETHER.md** (+ PDF): the teaching guide - the four kinds of pieces (code,
  settings, secrets, run-time choices), YAML in two minutes, which files you edit (only
  `poam/poam.csv`), one setting traced from its default to a host (precedence), one inventory with
  many groups and a host in several, modularity, surveys, and updating the work repository.
  Appendix: every role's settings.
- **scripts/update-from-release.ps1** (Windows / VS Code, PowerShell 5.1 and 7, Constrained
  Language Mode safe) and **scripts/update-from-release.sh** (Linux): update a work copy from a
  release safely - refuses a copy with uncommitted changes, previews NEW / CHANGED / DELETE
  (ignoring Windows vs Linux line endings), never touches `poam/poam.csv`, `inventories/site/`,
  `.site-local` and the paths it lists, or git-ignored files. Tested on a Windows CI runner.
- **.gitattributes**: Linux line endings (LF) in Git even when committed from Windows (the
  playbooks' shell commands break with CRLF).
- Releases now also come as a **.zip** (right-click > Extract All on Windows).
- **docs/USING_YOUR_AAP_INVENTORY.md**: for an inventory already in AAP. The groups to create
  (including the safety groups), which variables go on the inventory, which group or host, and
  which repository files not to touch. Linked from SETUP_AAP and START_HERE, and in the PDF.

## 0.3.0 — 2026-09-26

Operations runbooks: health checks, troubleshooting, ServiceNow, POA&M, patching, and a modular
way to add ACT to any of them. Built-in modules only (Minimal execution environment); tested with
ansible-core 2.16 and 2.21.

- **Health check** (`playbooks/health_check.yml`, `roles/check_*`): 14 read-only checks, picked per
  run or by the `daily` / `weekly` / `all` shortcuts - disk (space, inodes, days-until-full
  forecast), mounts (fstab vs mounted, read-only remounts, hung NFS/CIFS), services (failed units,
  required/enabled, restart loops), performance (load, memory, swap, iowait, steal, OOM kills,
  zombies), time (chrony), network (route, DNS, TCP reachability), logging (persistent journal,
  log server, rsyslog queue, error rate), SELinux (mode, config, permissive domains, AVCs,
  booleans, labels), fapolicyd (running, enforcing, config, trust DB, denials), auditd (running,
  rules, lost events, log space, forwarding), accounts (UID 0, empty passwords, inactive,
  NOPASSWD, expiring service-account passwords), certs (files, chains, Java keystores, TLS
  endpoints), patching (security advisories, reboot needed, patch age), MariaDB health
  (running, connections, long queries without query text, replication, Galera, error log, data
  disk; host or container; root socket or a least-privilege monitor account). Every check gives
  the same answer in Check mode.
- **Findings contract** (`roles/site_findings`): check / id / severity / summary / hint. A check
  that breaks becomes a finding; the report and set_stats artifacts are published before the job
  fails, and hosts that never reported are tracked.
- **Troubleshoot** (`playbooks/troubleshoot.yml`): per problem area, the commands an admin runs
  first, each explained, plus the matching checks.
- **Certificate report** (`playbooks/cert_report.yml`): one fleet table, soonest expiry first.
- **ServiceNow** (`playbooks/servicenow_tickets.yml`, `servicenow_health.yml`, `roles/servicenow`):
  REST Table API with `uri`; one incident per finding (correlation ID), work notes on repeats,
  tickets for hosts that never reported, cleared problems noted or resolved, ACT's analysis in the
  description; dry run without the credential. Instance API and MID Server health.
- **POA&M status** (`playbooks/poam_status.yml`, `roles/poam`, `poam/`): overdue / due-soon items
  from a CSV (eMASS export); optional STIG Manager cross-check (client-credentials token; the
  realm owner creates the client) for open CAT I/II findings with no POA&M item.
- **Patch hosts** (`playbooks/patch_hosts.yml`, `roles/patch`): serial, stops at the first
  failure, free-space guard, reboot only when needed and never for `aap_hosts`, every running
  service must come back; skips `no_patch`; refuses the local machine.
- **ACT, modular** (`roles/site_act`, `playbooks/act_fix_approved.yml`): `use_act=true` on any
  check playbook; levels explain (evidence only, ACT runs nothing) / diagnose (proposals wait
  for approval) / self-heal (only `site_act_allow`, then the checks run again). Diagnose-only
  for `aap_hosts` and `netapp_console_hosts`.
- **Docs**: START_HERE, SETUP_AAP (step by step), RUNBOOKS, WORKFLOWS_AND_SCHEDULES, ADDING_ACT;
  new credential types (ServiceNow API, MariaDB monitor, STIG Manager API, Keystore password);
  example inventory with groups and settings for every runbook.
- **PDFs in `docs/pdf/`**: the runbooks setup guide (rendered from the docs, with diagrams), the
  service-watch demo, the ACT triage guide and AAP 2.7 runbook, and the two leadership briefs.
- CI: syntax check with ansible-core 2.16 as well as the latest, filter unit tests, YAML checks;
  `.ansible-lint` (production profile passes) and `.yamllint`.

## 0.2.0 — 2026-09-25

- **Service watch** (`playbooks/service_watch.yml`, `playbooks/service_fix_approved.yml`,
  `roles/service_watch`): watches podman containers (default `stigman`, `nginx`; optional page
  URL and systemd unit per container). When one is down: records it, lets ACT (GenAI) find the
  root cause, and either hands the proposed fix to an AAP approval step (default) or self-heals
  (ACT may only start/restart the watched containers). The apply job runs exactly the approved
  command. The playbook re-checks the containers itself after any fix. The check job fails only
  when a person is needed, so the workflow's "Run on fail" link raises an approval only then.
  Incidents go to `/var/log/service-watch/incidents.jsonl`, syslog (tag `service-watch`) and AAP
  job history. Step-by-step guide: `docs/SERVICE_WATCH_DEMO.md`.
- Vendored ACT-Linux 0.6.18: host names, IP addresses, user names and e-mail addresses are
  pseudonymized before anything reaches the model; the role passes each host's inventory name.

## 0.1.0 — 2026-09-25

First release.

- **STIG Manager deployment** (`playbooks/stigman_deploy.yml`, `roles/stigman_stack`): MySQL 8.4,
  STIG Manager and nginx (TLS) on podman, managed by systemd through Quadlet units on a private
  network. Keycloak is external (existing realm, never touched). Secrets come from AAP
  credentials and become podman secrets over stdin: never on a command line, in a unit file, in
  `podman inspect`, or in job output. Idempotent; restarts only what changed and never restarts
  MySQL during its first-time initialisation; refuses to silently replace a deployed database
  password. Tested end to end with rootless podman 5.8; the rootful path is the same role and
  has not yet been run as root.
- **AAP credential types** (`aap/credential_types/`): STIG Manager database, TLS certificate,
  container registry login (hosts), ACT model key, Teams webhook. Environment and file
  injectors only.
- **Secrets guide** (`docs/SECRETS.md`): where each secret lives, the rules every playbook
  follows, the real trust boundary, and rotation.
- **ACT-Linux 0.6.17 vendored** in `vendor/act`, refreshed with `scripts/update-act.sh`.
- Example inventory with placeholders only; CI syntax-checks every playbook.
