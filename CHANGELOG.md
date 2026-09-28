# Changelog

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
