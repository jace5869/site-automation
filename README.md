# Site automation

Runbooks for the site's Linux servers, run from Ansible Automation Platform 2.7 (or the Ansible
CLI): **health checks** (system, compliance, MariaDB, certificates, patching), **troubleshooting**,
**ServiceNow** tickets and health, **POA&M** status, **patching** with an approval, the **STIG
Manager** deployment, and ACT (GenAI) monitoring with approved fixes.

**New to Ansible or AAP? Start with [docs/START_HERE.md](docs/START_HERE.md)**, then follow
[docs/SETUP_AAP.md](docs/SETUP_AAP.md) step by step. The same guide as one printable PDF:
[docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf](docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf).

**Secrets never live in this repository.** They are AAP credentials, injected at run time.
Read [docs/SECRETS.md](docs/SECRETS.md).

Only modules that ship with Ansible are used (no collections to install), so the Minimal
execution environment runs everything, also on a disconnected network. Tested with ansible-core
2.16 and 2.21, against Rocky Linux 9 test hosts (containers, MariaDB 10.5) and, read-only, a
Fedora host with SELinux enforcing and auditd.

## Runbooks

| Job template | Playbook | What |
|---|---|---|
| Health check | `playbooks/health_check.yml` | read-only checks, picked per run: disk (with days-until-full), mounts, services, performance, time, network, logging, SELinux, fapolicyd, auditd, accounts, certificates, patching, MariaDB. Findings in plain words, each with a command to look further; fails hosts with findings so a workflow can react |
| Troubleshoot | `playbooks/troubleshoot.yml` | on demand: the first commands an admin runs for a kind of problem (disk, performance, service, network, SELinux, fapolicyd, login, time, logs, MariaDB), each explained, plus the matching checks |
| Certificate report | `playbooks/cert_report.yml` | every certificate on every host (files, keystores, TLS endpoints) in one table, soonest expiry first |
| POA&M status | `playbooks/poam_status.yml` | overdue / due-soon POA&M items from `poam/poam.csv`; optional STIG Manager cross-check for open CAT I/II findings with no POA&M item |
| ServiceNow tickets | `playbooks/servicenow_tickets.yml` | workflow step: one incident per finding, updated (not duplicated) on later runs, noted or resolved when it clears |
| ServiceNow health | `playbooks/servicenow_health.yml` | the instance answers its API; MID Servers up and validated |
| ServiceNow - test ticket | `playbooks/servicenow_test_ticket.yml` | proves the setup: logs in, checks the assignment group, opens a test incident, reads it back, adds a work note, resolves it; PASS / WARN / FAIL per step with the likely cause ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md)) |
| Patch hosts | `playbooks/patch_hosts.yml` | dnf update one host at a time; **no automatic reboot** unless `automatic_restarts: true`; every service back afterwards; never patches AAP or vendor appliances |
| Apply approved ACT fix | `playbooks/act_fix_approved.yml` | after an approval: runs exactly the approved commands (no model), then the checks run again; as *Check* a dry run |
| Service watch (+ apply) | `playbooks/service_watch.yml`, `service_fix_approved.yml` | watch the stigman/nginx containers; ACT root cause; approval or self-heal ([docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md)) |
| STIG Manager - deploy | `playbooks/stigman_deploy.yml` | MySQL 8.4 + STIG Manager + nginx (TLS) on podman/Quadlet |
| **Windows** health check, troubleshoot, certificate report, connection test | `playbooks/win_*.yml` | the same for Windows servers over WinRM: disk, services, performance, time, network, event log, security, audit policy, accounts, certificates, patching ([docs/WINDOWS.md](docs/WINDOWS.md)) |
| Windows patch | `playbooks/win_patch.yml` | install what Software Center (ConfigMgr) offers, one server at a time; **no automatic restart** unless `automatic_restarts: true`; every service back afterwards; maintenance windows respected |
| ACT for Windows - install | `playbooks/win_act_install.yml` | ACT on the Windows servers in `C:\ProgramData\act`: locked down to Administrators and SYSTEM, unblocked, SHA256-checked every run |

## Documentation

| Doc | For |
|---|---|
| [docs/START_HERE.md](docs/START_HERE.md) | what the pieces are (Ansible and AAP in plain words) and how a run works |
| [docs/HOW_IT_FITS_TOGETHER.md](docs/HOW_IT_FITS_TOGETHER.md) | how code, inventory, variables, credentials and surveys fit; a setting's way from YAML to a host; updating the repository at work safely |
| [docs/SETUP_AAP.md](docs/SETUP_AAP.md) | the setup, step by step: Git, credentials, project, inventory, templates, ServiceNow |
| [docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md) | ServiceNow, step by step: the API account to ask for, the instance URL and API calls, firewall / proxy / DoD CA, the credential, a test ticket, and how to verify it in ServiceNow |
| [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md) | your inventory is already in AAP: the groups to add and which variables go on which group |
| [docs/RUNBOOKS.md](docs/RUNBOOKS.md) | every check and finding, and what to do about it |
| [docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md) | how to build any workflow, and 17 ready-made ones for Linux, Windows and both (health, compliance, patching with a dry run first, ACT fixes, certificates, MariaDB, investigations, STIG Manager); schedules |
| [docs/ADDING_ACT.md](docs/ADDING_ACT.md) | adding ACT later (explain / diagnose / self-heal), and writing your own check |
| [docs/WINDOWS.md](docs/WINDOWS.md) | Windows servers: WinRM, credential, inventory, templates, settings, patching, ACT |
| [docs/DRY_RUNS.md](docs/DRY_RUNS.md) | dry runs (Job type Check): what each template does in one, where to set it, examples |
| [docs/SECRETS.md](docs/SECRETS.md) | where every secret lives |
| [docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md) | the service-watch demo |
| [docs/pdf/](docs/pdf/README.md) | **PDFs** of the guides: the runbooks setup guide, the service-watch demo, the ACT guides and leadership briefs |

## Layout

| Path | What |
|---|---|
| `playbooks/` | one playbook per job template |
| `playbooks/group_vars/` | **your settings**, one file per AAP group (`all.yml` = every host); yours - updates never overwrite them |
| `roles/check_*/` | one role per health check; settings in `defaults/main.yml` |
| `roles/win_check_*/`, `roles/win_troubleshoot/` | the Windows checks (a PowerShell script each, in `files/`) and Windows troubleshooting |
| `roles/site_findings/` | the findings contract: start, run a check safely, report, publish, pass/fail |
| `roles/site_act/` | the bridge from any check's findings to ACT |
| `roles/troubleshoot/`, `roles/servicenow/`, `roles/patch/`, `roles/poam/` | the other runbooks |
| `roles/service_watch/`, `roles/stigman_stack/` | service watch, STIG Manager deployment |
| `plugins/filter/` | small filters (CSV reading, dates) - standard library only |
| `aap/credential_types/` | the custom credential types to create in AAP (input + injector YAML) |
| `inventories/example/` | placeholder inventory and settings: copy to `inventories/site/` |
| `poam/` | the POA&M list (CSV) for `poam_status.yml` |
| `vendor/act/` | ACT-Linux (the `act` tool and its roles), vendored from a release |
| `scripts/update-act.sh` | refresh `vendor/act` from a newer ACT-Linux release |
| `scripts/update-from-release.ps1` | Windows (VS Code): update YOUR copy from a new site-automation release - preview first; your own files (`poam/poam.csv`, `.site-local` paths, git-ignored files) are never touched |
| `scripts/update-from-release.sh` | the same on Linux |

## Using it in AAP

Follow [docs/SETUP_AAP.md](docs/SETUP_AAP.md). In short: put this repository in your work Git
with your inventory in `inventories/site/`; create the credential types in `aap/credential_types/`
and the credentials; a project on the repository; an inventory sourced from
`inventories/site/hosts.yml`; one job template per playbook; then the workflows and schedules in
[docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md).

Job template **STIG Manager - deploy**: playbook `playbooks/stigman_deploy.yml`; credentials:
Machine, *STIG Manager database*, *TLS certificate* (and *Container registry login (hosts)* if
you pull with an account). It runs on the group `stigman` (or set `target`).

## The STIG Manager deployment

- Rootful podman, managed by systemd through Quadlet unit files in `/etc/containers/systemd/`:
  `stigman-mysql`, `stigman-api`, `stigman-nginx`, on a private `stigman` network. Only nginx
  publishes a port (443).
- Keycloak is external and its realm already exists: the role only needs its URL
  (`stigman_oidc_provider`) and trusts your CA chain for it.
- Needs podman 4.4 or newer (RHEL 9.2+ / 8.8+).
- Idempotent: a re-run with nothing changed reports `changed=0`. It restarts only services whose
  inputs changed (a new certificate restarts nginx only) and never restarts MySQL during its
  first-time initialisation.
- It **refuses** to replace a database password that differs from the deployed one; rotation is
  a deliberate, separate run (see docs/SECRETS.md).

Tested end to end with rootless podman 5.8 (the same role with `stigman_scope: user`), with STIG
Manager's demo Keycloak standing in for your realm. The rootful path is the same role with
`stigman_scope: system`; it has not yet been run as root.

## Updating ACT

```bash
scripts/update-act.sh ACT-Linux-0.6.19.tar.gz   # a release tarball, or a path to an ACT-Linux checkout
git diff --stat && git commit -am "vendor ACT-Linux 0.6.19"
```
