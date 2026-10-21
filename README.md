# Site automation

Runbooks for the site's Linux servers, run from Ansible Automation Platform 2.7 (or the Ansible
CLI): **health checks** (system, compliance, MariaDB / MySQL, certificates, patching), **troubleshooting**,
**ServiceNow** tickets and health, **POA&M** status, **patching** with an approval, the **STIG
Manager** deployment, and ACT (GenAI) monitoring with approved fixes.

**New to Ansible or AAP? Start with [docs/START_HERE.md](docs/START_HERE.md)**, then follow
[docs/SETUP_AAP.md](docs/SETUP_AAP.md) step by step. The same guide as one printable PDF:
[docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf](docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf).

**Secrets never live in this repository.** They are AAP credentials, injected at run time.
Read [docs/SECRETS.md](docs/SECRETS.md).

Only modules that ship with Ansible are used (no collections to install), so the Minimal
execution environment runs everything, also on a disconnected network. Tested with ansible-core
2.16 and 2.21, against Rocky Linux 9 test hosts (containers, MariaDB 10.5), MySQL 8.0 / 8.4 and
MariaDB 11.8 in rootless podman containers, and, read-only, a Fedora host with SELinux enforcing
and auditd.

**Updating from 0.4.1?** Read the upgrade notes at the top of [CHANGELOG.md](CHANGELOG.md): your
own `playbooks/group_vars/all.yml` keeps its old settings until you comment them out.

## Runbooks

| Job template | Playbook | What |
|---|---|---|
| Health check | `playbooks/health_check.yml` | read-only checks, picked per run: disk (with days-until-full), mounts, services, performance, time, network, logging, SELinux, fapolicyd, auditd, accounts, certificates, patching, MariaDB / MySQL (`mariadb`, `mysql` or `database`), podman containers (`containers`: root's and every user's, found by themselves). Findings in plain words, each with a command to look further; fails hosts with findings so a workflow can react |
| **Database health - MariaDB / MySQL** | `playbooks/database_health.yml` | **the database check on its own, for MariaDB and MySQL (5.7, 8.0, 8.4), installed on the host or in podman containers** (one, several, found automatically, rootless): running, connections, long queries, replication, Galera and Group Replication, log errors, disk. Read-only; fails hosts with findings ([docs/MARIADB.md](docs/MARIADB.md)) |
| Troubleshoot | `playbooks/troubleshoot.yml` | on demand: the first commands an admin runs for a kind of problem (disk, performance, service, network, SELinux, fapolicyd, login, time, logs, MariaDB / MySQL), each explained, plus the matching checks |
| Certificate report | `playbooks/cert_report.yml` | every certificate on every host (files, keystores, TLS endpoints) in one table, soonest expiry first; Satellite left out (`cert_skip_hosts`, `cert_skip_groups`) |
| Certificate renewal - CSRs | `playbooks/cert_renew_csr.yml` | for each certificate file that expires: a new key and a CSR (same subject and names) on the host, the CSR attached to the certificate's ServiceNow incident; never Satellite's or subscription-manager's certificates |
| Windows certificate renewal - CSRs | `playbooks/win_cert_renew_csr.yml` | the same on Windows servers: a new key and CSR with `certreq`, on the certificate's ServiceNow ticket; the key stays on the server |
| Network device certificate report / renewal | `playbooks/net_cert_report.yml`, `net_cert_renew.yml` | the certificates of switches, firewalls, printers, iLO / iDRAC ... read from the controller (an inventory group, or subnets swept) - no login to the devices; the renewal opens an incident per certificate due, and can make the CSR on Cisco IOS / IOS-XE ([docs/NETWORK_CERTS.md](docs/NETWORK_CERTS.md)) |
| Network config backup / load | `playbooks/net_config_backup.yml`, `net_config_load.yml` | Cisco IOS / IOS-XE and Palo Alto PAN-OS: each running configuration kept in a private git repository (a commit when one changed) or on a file share, the diff in the report and optionally an incident; the load sends lines or restores a backup, after an approved change request, tried in Check first ([docs/NETWORK_CONFIG.md](docs/NETWORK_CONFIG.md)) |
| (every certificate report) | `cert_inventory_files`, `cert_inventory_sql` | every certificate with the same fields - issuer, names, key, fingerprints, tier (root, intermediate, CA-signed, self-signed) - as CSV / JSON files or a SQL Server table, for a dashboard such as Power BI ([docs/CERT_INVENTORY.md](docs/CERT_INVENTORY.md)) |
| POA&M status | `playbooks/poam_status.yml` | overdue / due-soon POA&M items from `poam/poam.csv`; optional STIG Manager cross-check for open CAT I/II findings with no POA&M item |
| ServiceNow tickets | `playbooks/servicenow_tickets.yml` | workflow step: one incident per finding, updated (not duplicated) on later runs, noted or resolved when it clears |
| ServiceNow health | `playbooks/servicenow_health.yml` | the instance answers its API; MID Servers up and validated |
| ServiceNow - catalog test | `playbooks/servicenow_catalog_test.yml` | for the certificate renewals ordering the PKI's catalog item (`cert_renew_servicenow: catalog`, no incidents): lists the item's questions by their internal names, checks the settings, and can order it once with a test CSR ([docs/CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)) |
| ServiceNow - test ticket | `playbooks/servicenow_test_ticket.yml` | proves the setup: logs in, checks the assignment group, opens a test incident, reads it back, adds a work note, resolves it; PASS / WARN / FAIL per step with the likely cause ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md)) |
| Patch hosts | `playbooks/patch_hosts.yml` | dnf update one host at a time; **no automatic reboot** unless `automatic_restarts: true`; every service back afterwards; never patches AAP or vendor appliances |
| Apply approved ACT fix | `playbooks/act_fix_approved.yml` | after an approval: runs exactly the approved commands (no model), then the checks run again; as *Check* a dry run |
| Service watch (+ apply) | `playbooks/service_watch.yml`, `service_fix_approved.yml` | watch the podman containers that should be running (found by themselves: root's and each user's, plus any you list); ACT root cause; approval or self-heal ([docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md)) |
| STIG Manager - deploy | `playbooks/stigman_deploy.yml` | MySQL 8.4 + STIG Manager + nginx (TLS) on podman/Quadlet |
| **Windows** health check, troubleshoot, certificate report, connection test | `playbooks/win_*.yml` | the same for Windows servers over WinRM: disk, services, performance, time, network, event log, security, audit policy, accounts, certificates, patching ([docs/WINDOWS.md](docs/WINDOWS.md)) |
| Windows patch | `playbooks/win_patch.yml` | install what Software Center (ConfigMgr) offers, one server at a time; **no automatic restart** unless `automatic_restarts: true`; every service back afterwards; maintenance windows respected |
| **VMware (vCenter)**: restart, shut down, snapshot (no memory), delete snapshot, notes, change VLAN, secure boot report, datastore report, snapshot report and cleanup (with an approval workflow), health report (every datacenter, cluster and host: HA, DRS, vCLS, hosts, hardware), alarms report (also the alarms that fired and cleared) and its ACT analysis (likely cause, fix, confidence), ESXi security settings every night (SSH and ESXi Shell off, timeouts, lockdown normal), vCenter and ESXi certificates (a report, and a renewal: an incident and a CSR for each one that expires soon), capacity planning overall and per cluster (runways, hosts recommended, math vs GenAI); the reports cover several vCenters in one (`vmware_vcenters`); a weekly report of the VMs created (by whom, from which ticket); deploy VMs from a template for a ServiceNow ticket (one or several per ticket) | `playbooks/vm_*.yml`, `playbooks/esxi_security.yml`, `playbooks/vmware_cert_report.yml`, `playbooks/vmware_cert_renew.yml` | jobs on virtual machines through vCenter; restart / shut down / VLAN change never touch the AAP server's VM; dry run as *Check* ([docs/VMWARE.md](docs/VMWARE.md)) |
| **VMware pick lists** | `playbooks/vm_pick_refresh.yml`, `vm_snapshot_delete_picked.yml`, `vm_reboot_picked.yml` | choose at launch which snapshots to delete or which VMs to restart (or only the hung ones), from a survey list a scheduled job keeps current; each pick checked again live, then plan, approval, exactly the plan ([docs/VMWARE.md](docs/VMWARE.md), "Pick lists") |
| **Active Directory** health report | `playbooks/ad_health_report.yml` | read-only, every domain controller over WinRM: replication (each DC's partners, and every DC's latest change reaching the PDC), dcdiag, FSMO roles, SYSVOL / DFSR, DNS SRV records, time, DC events, LDAPS / KDC certificates, backups, RID pool, krbtgt, trusts, sites - and **what changed** in the last day: privileged group members added / removed (when, on which DC, by whom), accounts created / deleted / disabled / unlocked, GPOs and their links, schema, sites, subnets, DCs - coloured, problems first, emailed ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md)) |
| **Active Directory** account lockout | `playbooks/ad_lockout.yml` | for the service desk: is an account locked out, and **which computer is locking it out** - every DC's own bad password count and its Security log (4740 and the caller computer, 4771 / 4768 Kerberos and the client's address, 4776 NTLM and the workstation, 4625), the sources ranked with what each one usually is (a phone, a service, a scheduled task, a mapped drive, a disconnected Remote Desktop session); empty = every account locked out now; optional unlock on the PDC emulator, and a look on the source computers of the inventory ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md#account-lockout)) |
| **NetApp ONTAP** health report | `playbooks/ontap_health_report.yml` | read-only (REST GET): clusters, nodes, health alerts, events, hardware, disks, network and CRC errors, aggregates, volumes, inodes, snapshots, SnapMirror, peers, SVMs, CIFS, LUNs, certificates, time - coloured, problems first, emailed ([docs/NETAPP.md](docs/NETAPP.md)) |
| **Horizon 8 (VDI)**: API test, health report, capacity report, broken desktops (check -> approval -> fix), user help for the service desk, disconnected session cleanup, golden image push (plan -> approval -> push) | `playbooks/horizon_*.yml` | the Connection Server's REST API from the AAP side, any Horizon 8 release (each path's API version found by asking); instant clones recovered, sessions logged off, images pushed only after approval or when asked; dry run as *Check* ([docs/HORIZON.md](docs/HORIZON.md)) |
| ACT for Windows - install | `playbooks/win_act_install.yml` | ACT on the Windows servers in `C:\ProgramData\act`: locked down to Administrators and SYSTEM, unblocked, SHA256-checked every run |

## Documentation

| Doc | For |
|---|---|
| [docs/START_HERE.md](docs/START_HERE.md) | what the pieces are (Ansible and AAP in plain words) and how a run works |
| [docs/HOW_IT_FITS_TOGETHER.md](docs/HOW_IT_FITS_TOGETHER.md) | how code, inventory, variables, credentials and surveys fit; a setting's way from YAML to a host; updating the repository at work safely |
| [docs/SETUP_AAP.md](docs/SETUP_AAP.md) | the setup, step by step: Git, credentials, project, inventory, templates, ServiceNow |
| [docs/VMWARE.md](docs/VMWARE.md) | the VMware jobs: the collection the execution environment needs, the vCenter account and credential, one template per job with its survey |
| [docs/VM_DEPLOY_GUIDE.md](docs/VM_DEPLOY_GUIDE.md) | the VM deploy from a ServiceNow ticket, complete: setting it up in AAP step by step, how a run works, what requesters write, where the VM goes, configurations to copy, every message with what to do, every setting (also a PDF) |
| [docs/EXECUTION_ENVIRONMENT.md](docs/EXECUTION_ENVIRONMENT.md) | one execution environment for everything (Linux, Windows and Active Directory, VMware, NetApp, MySQL / MariaDB / SQL Server, Cisco, ServiceNow): build it from `ee/all-in-one/`, check it, add it to AAP; AD as an inventory (also a PDF) |
| [docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md) | the Active Directory health report: the account it needs, the template, every section (and where "when", "which DC" and "by whom" come from), settings and troubleshooting; the account lockout job for the service desk (the audit policy, the survey, the unlock) |
| [docs/NETAPP.md](docs/NETAPP.md) | the NetApp ONTAP health report: the read-only ONTAP account, the credential, the template, every section, settings and troubleshooting |
| [docs/HORIZON.md](docs/HORIZON.md) | the Horizon 8 (VDI) jobs: the service account and its Horizon role, the credential, the templates and surveys, the broken desktops and image push workflows step by step, what is not yet tried on a real Horizon |
| [docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md) | emailed reports: which jobs email, setting it up, green or red when a report finds problems, the look, adding email to a playbook |
| [docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md) | ServiceNow, step by step: the API account to ask for, the instance URL and API calls, firewall / proxy / DoD CA, the credential, a test ticket, and how to verify it in ServiceNow |
| [docs/CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md) | the certificate renewals without incidents: each CSR ordered from your PKI's catalog item - finding the item's questions, the settings, the catalog test, what a run does |
| [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md) | your inventory is already in AAP: the groups to add and which variables go on which group |
| [docs/VARIABLES.md](docs/VARIABLES.md) | settings (variables): what you can set, where to put it (settings file, AAP Variables box, extra variables), and which place wins; with [docs/VARIABLES_REFERENCE.md](docs/VARIABLES_REFERENCE.md), every setting and its default (generated) |
| [docs/APPROVED_COMMANDS.md](docs/APPROVED_COMMANDS.md) | how to pre-approve the commands ACT may run by itself, what can never be approved, and how to verify it |
| [docs/MARIADB.md](docs/MARIADB.md) | the MariaDB and MySQL database checks, step by step: host installs and podman containers (rootless too), the monitor account per engine, verifying it, what was tested |
| [docs/RUNBOOKS.md](docs/RUNBOOKS.md) | every check and finding, and what to do about it |
| [docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md) | how to build any workflow, and 17 ready-made ones for Linux, Windows and both (health, compliance, patching with a dry run first, ACT fixes, certificates, MariaDB, investigations, STIG Manager); schedules |
| [docs/ADDING_ACT.md](docs/ADDING_ACT.md) | adding ACT later (explain / diagnose / self-heal), and writing your own check |
| [docs/WINDOWS.md](docs/WINDOWS.md) | Windows servers: WinRM, credential, inventory, templates, settings, patching, ACT |
| [docs/DRY_RUNS.md](docs/DRY_RUNS.md) | dry runs (Job type Check): what each template does in one, where to set it, examples |
| [docs/SECRETS.md](docs/SECRETS.md) | where every secret lives |
| [docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md) | the service-watch demo |
| [docs/pdf/](docs/pdf/README.md) | **PDFs** of the guides: the runbooks setup guide, the VM deploy guide, the service-watch demo, the ACT guides and leadership briefs |

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
| `roles/ad_report/` | the Active Directory health report: the PowerShell that reads each DC and the domain (`files/`), the report in `plugins/filter/ad_filters.py` |
| `roles/ad_lockout/` | the account lockout job: the PowerShell that each DC (and each source computer) runs (`files/`), the report in `plugins/filter/ad_lockout_filters.py` |
| `plugins/filter/` | small filters (CSV reading, dates) - standard library only |
| `aap/credential_types/` | the custom credential types to create in AAP (input + injector YAML) |
| `inventories/example/` | placeholder inventory and settings: copy to `inventories/site/` |
| `poam/` | the POA&M list (CSV) for `poam_status.yml` |
| `vendor/act/` | ACT-Linux (the `act` tool and its roles), vendored from a release |
| `vendor/act-windows/` | ACT-Windows (`act.ps1`), vendored from a release, for the *ACT for Windows - install* template |
| `scripts/update-act.sh` | refresh `vendor/act` from a newer ACT-Linux release |
| `scripts/update-act-windows.sh`, `.ps1` | refresh `vendor/act-windows` from a newer ACT-Windows release |
| `scripts/update-from-release.ps1` | Windows (VS Code): update YOUR copy from a new site-automation release - preview first; your own files (`poam/poam.csv`, `.site-local` paths, git-ignored files) are never touched |
| `scripts/update-from-release.sh` | the same on Linux |
| `scripts/gen_variable_reference.py` | rewrites `docs/VARIABLES_REFERENCE.md` from the roles' defaults (`--check` in CI) |
| `docs/pdf/` | the PDFs and the scripts that build them (`docs/pdf/README.md`) |

## Using it in AAP

Follow [docs/SETUP_AAP.md](docs/SETUP_AAP.md). In short: put this repository in your work Git
with your inventory in `inventories/site/`; create the credential types in `aap/credential_types/`
and the credentials; a project on the repository; an inventory sourced from
`inventories/site/hosts.yml`; one job template per playbook; then the workflows and schedules in
[docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md). Your inventory is already in
AAP? Keep it, and follow [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md)
instead of the inventory steps.

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
scripts/update-act.sh ACT-Linux-<version>.tar.gz           # a release tarball, or a path to an ACT-Linux checkout
scripts/update-act-windows.sh ACT-Windows-<version>.zip    # the same for ACT-Windows (Windows: update-act-windows.ps1)
bash tests/check_vendor.sh                                 # the copies match their VERSION files and vendor/CHECKSUMS
git diff --stat && git commit -am "vendor ACT <version>"
```
