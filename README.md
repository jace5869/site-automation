# Site automation

Runbooks for a site's servers and infrastructure. Works on **Ansible Automation Platform 2.5 and later** (menu names are
from AAP 2.7; also runs from the Ansible CLI). What started as Linux health checks now covers **Linux and Windows servers, VMware (vCenter and ESXi),
Horizon VDI, Active Directory, NetApp ONTAP, Cisco UCS and the network devices**, with **ServiceNow** for the tickets
and the reporting, **STIG Manager** for compliance, and **ACT (GenAI)** for diagnosis and fixes that a person
approves first.

Every job follows the same rules:

- **Reports are read-only.** A health check, a drift report, an inventory or a ticket report changes nothing on the
  hosts or in the systems it reads. Each finding is in plain words, with the command to look further.
- **Changes need an approval.** Patching, a certificate install, a fix ACT proposed, a STIG Manager update, a snapshot
  cleanup: a *check* or *plan* job shows exactly what would be done, a person approves it in AAP, and the *approved*
  job does only that. *Check* (a dry run) is a job type on every template.
- **Nothing automatic restarts.** No reboot or service restart unless you set `automatic_restarts: true`, and the AAP
  server and vendor appliances are never patched, restarted or changed.
- **Every report reaches people.** Emailed (green or red), a page on the report website, an Excel workbook where a
  table is long (with Excel's own trend lines where a table has a history); ServiceNow incidents opened, updated and resolved for the findings when you turn that on.
- **Levels in plain words.** Rows sort critical, warning, Low, info, ok. Low is worth knowing but needs nothing now: it
  always shows the word, never only a colour, and never turns a report amber. On the website (atlas and brief looks)
  tiles and trend lines show their numbers on hover or keyboard focus.

**New to Ansible or AAP? Start with [docs/START_HERE.md](docs/START_HERE.md)**, then follow
[docs/SETUP_AAP.md](docs/SETUP_AAP.md) step by step. The same guide as one printable PDF:
[guides/Site-Automation-Ops-Runbooks-Guide.pdf](guides/Site-Automation-Ops-Runbooks-Guide.pdf).

**Secrets never live in this repository.** They are AAP credentials, injected at run time.
Read [docs/SECRETS.md](docs/SECRETS.md). The repository holds placeholders only: your inventory goes in
`inventories/site/`, your settings in `playbooks/group_vars/`, and an update never touches either.

The Linux jobs use only modules that ship with Ansible (no collections to install), so the Minimal execution
environment runs them, also on a disconnected network. Windows, VMware, Active Directory and the network devices need
the collections of the execution environments in `ee/` ([docs/EXECUTION_ENVIRONMENT.md](docs/EXECUTION_ENVIRONMENT.md)).
Tested with ansible-core 2.16 and 2.21, against Rocky Linux 9 test hosts (containers, MariaDB 10.5), MySQL 8.0 / 8.4
and MariaDB 11.8 in rootless podman containers, a simulated vCenter and mocked ServiceNow, Horizon, ONTAP and UCS
APIs; and, read-only, a Fedora host with SELinux enforcing and auditd. What is not yet tried against the real
product says so in its guide.

**Updating your copy:** each release has a `changed-since` zip to paste over your copy, and
`scripts/update-from-release.sh` / `.ps1` do the same with a preview first; both leave your inventory, settings and
`poam/poam.csv` alone. The top of [CHANGELOG.md](CHANGELOG.md) says what changed and what, if anything, to set up.

## Runbooks

One job template per playbook. The table names the template as the guides call it; every template's one-line
card, with what it hands to the next job in a workflow, is in [docs/JOB_TEMPLATES.md](docs/JOB_TEMPLATES.md), and the
ready-made workflows and schedules in [docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md).

### Linux servers

| Job template | Playbook | What |
|---|---|---|
| Health check | `playbooks/health_check.yml` | read-only checks, picked per run: disk (with days-until-full), mounts, services, performance, time, network, logging, SELinux, fapolicyd, auditd, accounts, certificates, trust stores (`trust`: a compromised or dropped CA still trusted), patching, MariaDB / MySQL (`mariadb`, `mysql` or `database`), podman containers (`containers`: root's and every user's, found by themselves); with `patching`, a *Patch age* section: days since each host's last update by host group, late and due soon, % on time over 12 weeks. Findings in plain words, each with a command to look further; fails hosts with findings so a workflow can react |
| Linux - disk space report / tmp cleanup | `playbooks/disk_report.yml`, `tmp_cleanup.yml` | every mounted filesystem and LVM thin pool or snapshot, 85% amber and 90% red, the volume groups' free space and the old tmp files on a full filesystem - a report that stays green; then, after an approval (workflow 42), the old files of `/tmp` and `/var/tmp` deleted on those hosts, never sockets or systemd's own ([docs/DISK_SPACE.md](docs/DISK_SPACE.md)) |
| **Linux - software inventory** | `playbooks/linux_sw_inventory.yml` | Nightly RPM package inventory and changes, exported as shield text, CSV and JSON to one or more Windows file shares and email ([docs/LINUX_SW_INVENTORY.md](docs/LINUX_SW_INVENTORY.md)) |
| **Database health - MariaDB / MySQL** | `playbooks/database_health.yml` | Read-only MariaDB and MySQL check (host or podman containers): status, connections, long queries, replication, errors and disk ([docs/MARIADB.md](docs/MARIADB.md)) |
| Troubleshoot | `playbooks/troubleshoot.yml` | on demand: the first commands an admin runs for a kind of problem (disk, performance, service, network, SELinux, fapolicyd, login, time, logs, MariaDB / MySQL), each explained, plus the matching checks |
| **Log analysis** | `playbooks/log_analysis.yml`, `vm_log_analysis.yml` | Groups warning and error logs across Linux, Windows or vCenter into patterns with ACT root-cause analysis and emailed report ([docs/LOG_ANALYSIS.md](docs/LOG_ANALYSIS.md)) |
| fapolicyd - check / approved, fapolicyd denials | `playbooks/fapolicyd.yml`, `fapolicyd_denials.yml` | fapolicyd in one pass on each host: what keeps it down, what it blocks, its rules - one report and one list of changes to approve, then exactly the approved lines (workflow 36, daily); the denials report reads what fapolicyd blocked, with ACT's reading of each denial ([docs/RUNBOOKS.md](docs/RUNBOOKS.md#fapolicyd-playbooksfapolicydyml)) |

### Patching

| Job template | Playbook | What |
|---|---|---|
| Patch hosts | `playbooks/patch_hosts.yml` | dnf update one host at a time, or in batches (`patch_serial`); optionally a VMware snapshot `patching` of each VM right before it is patched (`patch_snapshot`); **no automatic reboot** unless `automatic_restarts: true`; every service back afterwards; never patches AAP or vendor appliances |
| Windows patch | `playbooks/win_patch.yml` | install what Software Center (ConfigMgr) offers, one server at a time; **no automatic restart** unless `automatic_restarts: true`; every service back afterwards; maintenance windows respected |

### Compliance: STIG and POA&M

| Job template | Playbook | What |
|---|---|---|
| STIG drift report | `playbooks/stig_drift_report.yml` | each Linux host scanned against the DISA STIG with OpenSCAP (the SCAP Security Guide's STIG profile: RHEL 9 V2R9, RHEL 10 V1R2), the scan's report kept on the host, and compared with the host's checklist in STIG Manager - read-only, never written to - and with its earlier scans: a rule STIG Manager has as Not a Finding that passed before and fails now (drift, CAT I critical), a rule that passed before and fails now (new); where the scan and the checklist disagree, what is fixed but still Open in STIG Manager ([docs/STIG_DRIFT.md](docs/STIG_DRIFT.md)) |
| Windows STIG drift report | `playbooks/win_stig_drift_report.yml` | the same on Windows servers with Evaluate-STIG - every STIG that applies (Windows Server 2019 / 2022 / 2025, Defender, Firewall, .NET, IIS, Edge ...), run as SYSTEM or the checklists it already wrote read - each checklist compared with the server's asset in STIG Manager and with its earlier scans ([docs/STIG_DRIFT.md](docs/STIG_DRIFT.md#windows-servers)) |
| STIG recheck | `playbooks/stig_recheck.yml`, `win_stig_recheck.yml` | the rules STIG Manager has Open or Not Reviewed for each host checked again on it - only those (Evaluate-STIG's -SelectVuln, oscap's --rule): minutes, not a full scan - and sorted: fixed but still Open (set Not a Finding), Not Reviewed the scan answered, still open, by hand; report only, the drift report's history left alone; Linux and Windows (every STIG of the server's asset and its web site and database assets) ([docs/STIG_DRIFT.md](docs/STIG_DRIFT.md#the-stig-recheck)) |
| STIG Manager - update | `playbooks/stigman_update.yml` | workflows 37 and 40: the recheck's "update the review" (or the imported checklists' reviews) written to STIG Manager after an approval - Open and passes now (Not a Finding), Not Applicable now, Not Reviewed and answered; each review only if unchanged since the check, read back after; nothing else is changed ([docs/STIG_DRIFT.md](docs/STIG_DRIFT.md#updating-stig-manager-with-an-approval)) |
| STIG Manager - import checklists | `playbooks/stigman_ckl_import.yml` | workflow 40: checklists (CKL, CKLB) from a share - VMware's InSpec ones too, matched to the installed STIG by their rules - written to STIG Manager after an approval, a missing asset created with its STIGs; Accepted reviews left; or on a schedule as STIG Manager Watcher - written at once to whichever collection has the host (a staging collection for the new), the files done with moved to `imported/`; 1.58.0: a STIG compliance scorecard first in its report, by a survey choice - open findings per collection, the week's change, the top rules ([docs/CKL_IMPORT.md](docs/CKL_IMPORT.md)) |
| Evaluate-STIG - update | `playbooks/evaluate_stig_update.yml` | Evaluate-STIG on the RHEL hosts updated from the zip you keep in your own repository - only where it is newer; your answer files carried over, the old folder kept, a quick run that puts the old one back if the new one does not work; Check lists each host's version ([docs/EVALUATE_STIG_UPDATE.md](docs/EVALUATE_STIG_UPDATE.md)) |
| POA&M status | `playbooks/poam_status.yml` | overdue / due-soon POA&M items from `poam/poam.csv`; optional STIG Manager cross-check for open CAT I/II findings with no POA&M item |
| STIG Manager - deploy | `playbooks/stigman_deploy.yml` | MySQL 8.4 + STIG Manager + nginx (TLS) on podman/Quadlet |
| STIG Manager - health / availability | `playbooks/stigman_health.yml`, `stigman_availability.yml` | the address and each node through the API (state, database and Keycloak connections, versions and schema alike, a login that reads the database, the database's connections and restarts), Keycloak, and on the hosts the containers, disk and MariaDB; every 5 to 15 minutes the availability - quiet when up, red when down ([docs/STIGMAN_HEALTH.md](docs/STIGMAN_HEALTH.md)) |

### Certificates and trust stores

| Job template | Playbook | What |
|---|---|---|
| Certificate report | `playbooks/cert_report.yml` | every certificate on every host (files, keystores, TLS endpoints) in one table, soonest expiry first; on the website an expiry timeline above it (the next 120 days by system, hover for each certificate; every certificate report: Linux, Windows, network devices, vCenter); Satellite left out (`cert_skip_hosts`, `cert_skip_groups`) |
| Trust store report | `playbooks/trust_report.yml` | which trust stores still hold a compromised or dropped CA - the system bundles, p11-kit, Java cacerts, Python certifi, Node, podman containers, an application's files - and the Go programs, Node and NSS below the version that fixed it; the eMudhra (emSign) list of October 2026 ships with it, yours in `check_trust_extra`; read-only, or with `check_trust_remove: true` it takes them out (blocklist, keystore, bundles - backups first, Check mode shows the plan) ([docs/TRUST_STORES.md](docs/TRUST_STORES.md)) |
| VMware - trust store report | `playbooks/vmware_trust_report.yml` | vCenter's trusted root store and each ESXi host's trusted CAs against the same list, on its own (a vCenter credential, no inventory); `check_trust_remove: true` deletes them, Check mode first ([docs/TRUST_STORES.md](docs/TRUST_STORES.md)) |
| Windows - trust store report | `playbooks/win_trust_report.yml` | Checks Windows certificate stores, Java `cacerts` and Python `certifi` against the dropped-CA list; optional removal after Check mode ([docs/TRUST_STORES.md](docs/TRUST_STORES.md#windows)) |
| Windows - local group members | `playbooks/win_local_groups.yml` | Read-only report of privileged local group members per server (CSV, JSON, xlsx), plus Evaluate-STIG answer file export ([docs/WINDOWS.md](docs/WINDOWS.md#local-group-members)) |
| Windows - schedule a restart | `playbooks/win_restart_schedule.yml` | Schedules one-time Windows restarts at a 24-hour site time, optionally only when a reboot is pending; Check shows the plan ([docs/WINDOWS.md](docs/WINDOWS.md#schedule-a-restart)) |
| Windows - cancel a scheduled restart | `playbooks/win_restart_cancel.yml` | removes those restart tasks (only ours) ([docs/WINDOWS.md](docs/WINDOWS.md#schedule-a-restart)) |
| Certificate renewal - CSRs | `playbooks/cert_renew_csr.yml` | Creates a new key and CSR on each Linux host for expiring certificates and attaches the CSR to ServiceNow; skips Satellite |
| Windows certificate renewal - CSRs | `playbooks/win_cert_renew_csr.yml` | Creates a new key and CSR on Windows servers with `certreq` and attaches the CSR to ServiceNow |
| Certificate install - check / approved | `playbooks/cert_install_check.yml`, `cert_install_approved.yml` | Installs issued certificates from PKI catalog requests after approval, reloads the service, checks TLS and rolls back on error ([docs/CERT_INSTALL.md](docs/CERT_INSTALL.md)) |
| Windows certificate install - check / approved | `playbooks/win_cert_install_check.yml`, `win_cert_install_approved.yml` | Installs issued Windows certificates with `certreq -accept`, moves IIS/RDP/WinRM bindings and rolls back on error ([docs/CERT_INSTALL.md](docs/CERT_INSTALL.md)) |
| VMware - certificate request | `playbooks/vmware_cert_request.yml` | Generates a CSR on named vCenters and ESXi hosts and orders it from the PKI catalog item on demand ([docs/VMWARE.md](docs/VMWARE.md#request-a-certificate-now)) |
| VMware - certificate install - check / approved | `playbooks/vmware_cert_install_check.yml`, `vmware_cert_install_approved.yml` | Installs issued vCenter and ESXi certificates from PKI catalog requests after approval ([docs/VMWARE.md](docs/VMWARE.md#installing-the-issued-certificates-of-vcenter-and-the-esxi-hosts-with-an-approval)) |
| Certificate restore / Windows certificate restore | `playbooks/cert_restore.yml`, `win_cert_restore.yml` | an installed certificate's old one back, from the copy the install made |
| PKI - DoD bundle check | `playbooks/pki_bundle_check.yml` | Weekly check of the DISA DoD PKI CA bundle against your local `cert_install_ca_bundle`; emails a newer verified `.p7b` ([docs/CERT_INSTALL.md](docs/CERT_INSTALL.md#keeping-the-ca-bundle-current)) |
| Network device certificate report / renewal | `playbooks/net_cert_report.yml`, `net_cert_renew.yml` | Reads TLS certificates of switches, firewalls and appliances from the controller with no login; renewal opens tickets and can create IOS CSRs ([docs/NETWORK_CERTS.md](docs/NETWORK_CERTS.md)) |
| (every certificate report) | `cert_inventory_files`, `cert_inventory_sql` | Exports all discovered certificates as CSV/JSON files or a SQL Server table for dashboards ([docs/CERT_INVENTORY.md](docs/CERT_INVENTORY.md)) |

### Network devices and the network

| Job template | Playbook | What |
|---|---|---|
| Network config backup / load | `playbooks/net_config_backup.yml`, `net_config_load.yml` | Backs up Cisco IOS/IOS-XE and PAN-OS configs to private git or a share with diffs; loads changes after an approved change request ([docs/NETWORK_CONFIG.md](docs/NETWORK_CONFIG.md)) |
| Network performance report | `playbooks/net_perf_report.yml` | Measures latency, jitter, packet loss, OCSP response, interface errors and optional iperf3 pairs against each host's baseline ([docs/NETWORK_PERF.md](docs/NETWORK_PERF.md)) |
| Mail relay whitelist | `playbooks/mail_relay_format.yml`, `mail_relay_plan.yml`, `mail_relay_approved.yml`, `mail_relay_unused.yml` | Formats Postfix `mynetworks`, adds or removes entries after approval, and plans removal of addresses unused for six months ([docs/MAIL_RELAY.md](docs/MAIL_RELAY.md)) |

### ServiceNow

| Job template | Playbook | What |
|---|---|---|
| ServiceNow tickets | `playbooks/servicenow_tickets.yml` | workflow step: one incident per finding, updated (not duplicated) on later runs, noted or resolved when it clears |
| ServiceNow health | `playbooks/servicenow_health.yml` | the instance answers its API; the MID Servers up, validated, reporting, on one version, their queue moving, no open issues - and on their Windows hosts the MID service, its agent log (hung? SEVERE lines), JVM restarts, the drive and the instance URL, in one report ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md#mid-servers)); a self-hosted instance's front-end nodes (units, stats page, logs, fapolicyd, disk), its database host (MariaDB, the database's size, growth and disk) and the cluster, in the same report ([Self-hosted instance](docs/SERVICENOW_SETUP.md#self-hosted-instance)) |
| ServiceNow - availability | `playbooks/servicenow_availability.yml` | every 5 to 15 minutes: the REST API and the login page, each asked a few times and timed - quiet when up, red when down, and the availability over a day, a week and a month from its own runs ([docs/AVAILABILITY.md](docs/AVAILABILITY.md)) |
| ServiceNow - weekly activity report | `playbooks/servicenow_war.yml` | everything an assignment group had in the week - incidents, catalog tasks, changes, change tasks, problem tasks: completed (with the date of closure), opened, still open and for how long, who had what; one line per task ready to paste into the WAR, an Excel workbook on the email, the page on the website; ACT's narrative when asked ([docs/SERVICENOW_WAR.md](docs/SERVICENOW_WAR.md)) |
| ServiceNow - ticket insights | `playbooks/servicenow_insights.yml` | Site and team ticket analytics: SLA breach alerts, top incident patterns with ACT fixes, trends, problem candidates and KB drafts ([docs/SERVICENOW_INSIGHTS.md](docs/SERVICENOW_INSIGHTS.md)) |
| ServiceNow - catalog test | `playbooks/servicenow_catalog_test.yml` | for the certificate renewals ordering the PKI's catalog item (`cert_renew_servicenow: catalog`, no incidents): lists the item's questions by their internal names, checks the settings, and can order it once with a test CSR ([docs/CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)) |
| ServiceNow - test ticket | `playbooks/servicenow_test_ticket.yml` | proves the setup: logs in, checks the assignment group, opens a test incident, reads it back, adds a work note, resolves it; PASS / WARN / FAIL per step with the likely cause ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md)) |

### Windows servers and Active Directory

| Job template | Playbook | What |
|---|---|---|
| **Windows** health check, troubleshoot, certificate report, connection test | `playbooks/win_*.yml` | the same for Windows servers over WinRM: disk, services, performance, time, network, event log, security, audit policy, accounts, certificates, patching (with the *Patch age* section, as on Linux) ([docs/WINDOWS.md](docs/WINDOWS.md)) |
| **Active Directory** health report | `playbooks/ad_health_report.yml` | Read-only DC health, replication matrix and 24-hour directory changes over WinRM, or via LDAP/DNS/Kerberos in reader mode ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md)) |
| **Active Directory** account lockout | `playbooks/ad_lockout.yml` | Finds which computer is locking out an account from DC Security logs, ranks likely sources, and can unlock on the PDC ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md#account-lockout)) |
| ACT - find and replace old copies | `playbooks/act_old_copies.yml` | an old `/usr/local/bin/act` on a host kept in `/opt/act/old` and linked to the jobs' ACT (`/opt/act/act`, updated when older, the old one kept too); Check first; `site_act_old_bin: report` only lists them |
| ACT for Windows - install | `playbooks/win_act_install.yml` | ACT on the Windows servers in `C:\ProgramData\act`: locked down to Administrators and SYSTEM, unblocked, SHA256-checked every run |

### VMware: vCenter and ESXi

| Job template | Playbook | What |
|---|---|---|
| **VMware (vCenter)** jobs and reports | `playbooks/vm_*.yml`, `playbooks/esxi_security.yml`, `playbooks/vmware_cert_report.yml`, `playbooks/vmware_cert_renew.yml` | VM actions (never the AAP VM), health/alarms/ACT analysis, snapshots, capacity planning, ESXi security, certificates and VM deploys; Check supported ([docs/VMWARE.md](docs/VMWARE.md), [docs/VM_DEPLOY_GUIDE.md](docs/VM_DEPLOY_GUIDE.md)) |
| VMware inventories and ESXi network check | `playbooks/vmware_version_report.yml`, `vmware_hardware_report.yml`, `vm_inventory_report.yml`, `vm_tools_report.yml`, `vm_rightsizing_report.yml`, `esxi_network_check.yml` | Read-only vCenter/ESXi versions, hardware, VMs, VMware Tools, right-sizing and vMotion/MTU network checks across vCenters ([docs/VMWARE.md](docs/VMWARE.md)) |
| **VM outage watch** | `playbooks/vm_outage_watch.yml` | Watches hosts, datastores, HA and hung VMs every 5–15 minutes; reports and tickets only on problems, with optional outage timeline review ([docs/VM_OUTAGE_WATCH.md](docs/VM_OUTAGE_WATCH.md)) |
| **VM - ask a question** | `playbooks/vm_ask.yml` | Plain-English read-only vCenter queries checked against a whitelist; refuses any change request ([docs/VM_ASK.md](docs/VM_ASK.md)) |
| **VMware ask** (the web page) | `playbooks/vmware_ask_deploy.yml` | Self-service `/ask/` web page behind STIG Manager's nginx (card login, read-only vCenter cache, audit log) ([docs/VMWARE_ASK.md](docs/VMWARE_ASK.md)) |
| **Site portal** (the web page) | `playbooks/site_portal_deploy.yml` | Web portal at `/portal/` with reports, approved AAP actions, live job view, ops-center wall, Ops monitor and ten looks ([docs/SITE_PORTAL.md](docs/SITE_PORTAL.md)) |
| **VMware pick lists** | `playbooks/vm_pick_refresh.yml`, `vm_snapshot_delete_picked.yml`, `vm_reboot_picked.yml` | Survey-driven snapshot deletes and VM restarts from refreshed pick lists, with plan and approval ([docs/VMWARE.md](docs/VMWARE.md), "Pick lists") |

### Horizon 8 (VDI)

| Job template | Playbook | What |
|---|---|---|
| **Horizon 8 (VDI)** jobs and reports | `playbooks/horizon_*.yml` | Health, capacity, events, settings, sessions, logon/FSLogix reports, user help, and approved desktop recovery, host maintenance and image push ([docs/HORIZON.md](docs/HORIZON.md)) |

### Storage, hardware and vulnerability scanning

| Job template | Playbook | What |
|---|---|---|
| **NetApp ONTAP** health report | `playbooks/ontap_health_report.yml`, `ontap_hardware_report.yml`, `ontap_capacity_trends.yml` | read-only (REST GET): clusters, nodes, health alerts, events, hardware, disks, network and CRC errors, aggregates, volumes, inodes, snapshots, SnapMirror, peers, SVMs, CIFS, LUNs, certificates, time - coloured, problems first, emailed; the hardware inventory of every cluster on its own, like the VMware one; and capacity trends, run daily: how fast the aggregates and volumes fill, the days until full, the volumes that fill again after a cleanup or a resize ([docs/NETAPP.md](docs/NETAPP.md)) |
| **NetApp - expand a volume** | `playbooks/ontap_volume_expand.yml`, `ontap_volume_list.yml` | grows one LUN or NFS volume picked from a survey list (10 %, 1-512 GB, or a size typed; a LUN's volume only as much as the bigger LUN needs; the aggregate stays at or under 97 %) and the VMware datastore on it (rescan + VMFS expand, or NFS refresh); everything the plan stood on is read again right before each change, autosize is never changed (grow_shrink, or a size over autosize's maximum, refuses), one run at a time, the report says the sizes read back; Check = the plan; the list is refreshed on a schedule ([docs/NETAPP.md](docs/NETAPP.md#expand-a-volume)) |
| **Cisco UCS** inventory | `playbooks/ucs_inventory_report.yml` | read-only (UCS Manager's XML API, HTTPS): every domain's fabric interconnects, chassis and IO modules, blades and rack servers with their serial numbers, service profiles, CPUs, memory, BIOS, CIMC, adapter and storage controller firmware, power supplies, every running firmware and the critical and major faults - emailed with an Excel workbook |
| **Network map** (SolarWinds) | `playbooks/netmap_report.yml` | read-only (SolarWinds Orion's SWIS REST API, SWQL queries only): the sites on a globe or a flat map with the WAN links between them (bandwidth, traffic in and out), every node and link of each site, what is down, and what changed since the last run - a plain email with a workbook, the map on the report website, and live on the site portal (docs/NETWORK_MAP.md) |
| **Java4Shell check** (Keycloak) | `playbooks/java4shell.yml` | CVE-2026-114938: reads the CPU and each Keycloak container's running Java (and every other Java process, ServiceNow included - reported only); with `j4s_fix` adds `-XX:-UseBASE64Intrinsics` to the compose file (a backup kept), with `j4s_restart` recreates Keycloak alone and reads the flag live; a one-file copy for an air-gapped box ([JAVA4SHELL.md](docs/JAVA4SHELL.md)) |
| **SolarWinds - snapshot** | `playbooks/solarwinds_snapshot.yml` | every 10 minutes: SolarWinds read once (SWQL queries only) and kept in the report database as one snapshot - nodes, links, interfaces, active alerts, events, SAM, volumes, VIM - so the reports read the database instead of SolarWinds (`docs/REPORT_DATABASE.md`) |
| **Tenable.sc (ACAS)**: kick off a scan of an inventory group from the scanner in each host's own datacenter - Tenable.sc's scan zones by address, by the host's `tenable_zone`, or by its site - one scan per zone; a first run lists the zones, scanners, policies and repositories; optionally wait and report the findings by severity and host | `playbooks/tenable_scan.yml` | on the AAP side with the Tenable.sc API credential; nothing is launched without a policy and a repository; dry run as *Check* ([docs/TENABLE.md](docs/TENABLE.md)) |

### ACT (GenAI): fixes with an approval, service watch

| Job template | Playbook | What |
|---|---|---|
| Apply approved ACT fix | `playbooks/act_fix_approved.yml` | after an approval: runs exactly the approved commands (no model), then the checks run again; as *Check* a dry run |
| Service watch (+ apply) | `playbooks/service_watch.yml`, `service_fix_approved.yml` | watch the podman containers that should be running (found by themselves: root's and each user's, plus any you list); ACT root cause; approval or self-heal ([docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md)) |

### Documents, and AAP itself

| Job template | Playbook | What |
|---|---|---|
| **Outage AAR** and **SOP** (Word documents) | `playbooks/incident_aar.yml`, `incident_sop.yml` (`incident_docs_survey.yml` adds the surveys) | what is typed in a survey - the outage's start, impact, detection, actions, root cause, recovery, lessons learned - as an after action report in Word with your logo and marking banner, and an SOP made from the AAR (its actions and recovery as the steps, or steps typed; or an SOP on its own); optional: ACT tidies the wording, suggests what may be missing, in a section of its own, and rewrites the AAR's actions as SOP steps; emailed only to the addresses typed, never on the report website |
| **AAP - health check** | `playbooks/aap_health_check.yml` | **read-only, for a containerized AAP 2.5+ host** (rootless podman, all components on one host), from AAP or from the command line on the host when AAP is down: OS, every AAP container and unit, the logs (the gateway proxy's 5xx and slow requests, HARAKIRI), the database (host variables per inventory), the API (instances, stuck jobs, cleanup jobs, each inventory's hosts page timed), receptor, certificates; PASS / WARN / FAIL with a hint, an HTML and a JSON report ([docs/AAP_HEALTH_CHECK.md](docs/AAP_HEALTH_CHECK.md)) |
| **AAP - backup / list backup targets / bundle for air-gap** | `playbooks/aap_backup.yml`, `aap_backup_targets.yml`, `aap_backup_bundle.yml` | **Phase 1 backup and air-gap move for containerized AAP 2.7** (single AAP host, non-root `ansible`, AAP only — no MariaDB, Site portal deploys separately on the STIG Manager host): read-only mount scan (`AAP - list backup targets`, never picks); preflight with coded stops (`SA-AAPBK-nn`), installer backup wrapper, exact build/release + verified SHA-256 manifest, optional retention and weekly Berlin schedule off by default (`AAP - backup`); self-contained carry-over bundle with backup, inventory, matching installer, custom EEs, collections and restore runbook PDF (`AAP - bundle for air-gap`) ([docs/AAP_BACKUP.md](docs/AAP_BACKUP.md), [guides/Site-Automation-AAP-Backup-Restore-Guide.pdf](guides/Site-Automation-AAP-Backup-Restore-Guide.pdf)) |
| AAP - report email question | `playbooks/aap_email_question.yml` | adds an optional **Also email the report to** box to every report template's survey (and their workflows) through AAP's API: whoever launches a report can send that run to more people, on top of the template's usual recipients ([docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md), "Also email the report to") |
| AAP - set execution environment | `playbooks/aap_ee.yml` | puts one execution environment on many job templates at once through AAP's API, after a preview; the AAP credential only ([docs/EXECUTION_ENVIRONMENT.md](docs/EXECUTION_ENVIRONMENT.md#step-3-use-it)) |
| AAP - set up portal actions | `playbooks/aap_portal_setup.yml` | makes or brings up to date, through AAP's API, the job templates and workflows the site portal's actions launch (`roles/site_portal_stack/files/workflows.yml`), with their approval nodes and links, and gives the portal's account Execute (Approve on a workflow with an approval node) and nothing more; never deletes, leaves what was changed by hand, Check lists every field it would change ([docs/SITE_PORTAL.md](docs/SITE_PORTAL.md#setting-it-up-in-aap)) |
| AAP - template variables audit | `playbooks/aap_template_audit.yml` | read-only: every job and workflow template's Variables through AAP's API - the settings that belong somewhere else (`ansible_*`, addresses, site-wide, the same in many templates, secrets - named, never shown) and where to move them; one report, a table per template; an auditor is enough ([docs/TEMPLATE_AUDIT.md](docs/TEMPLATE_AUDIT.md)) |
| Report website - copy | `playbooks/report_web_copy.yml` | with `report_web_copy: true` the report jobs write nothing on the web server: each keeps its page in AAP as a job artifact, and this one job, from an execution environment that can reach the server, copies the pages over ([docs/WEB_REPORTS.md](docs/WEB_REPORTS.md#the-copy-job-report-templates-in-any-execution-environment)) |

## AAP health check

*AAP - health check* (`playbooks/aap_health_check.yml`) is a read-only look at a containerized AAP host. Every check
gives PASS, WARN or FAIL with a detail and a hint. The HTML and JSON reports go to `/home/ansible/aap-health/` on the
host. It restarts, changes and installs nothing, and no check fails the job. The full guide is
[docs/AAP_HEALTH_CHECK.md](docs/AAP_HEALTH_CHECK.md).

**On the AAP host** (works when AAP is down), as the AAP account, from a copy of this repository:

```bash
cd ~/site-automation
ansible-playbook -i localhost, -c local -e target=localhost playbooks/aap_health_check.yml \
  -K -e @~/aap-health.vault.yml --ask-vault-pass                # add --tags db,api for some areas only
```

- `-K` is the sudo password, for the root-only reads (OOM kills, AVC and fapolicyd denials, dnf history).
- `~/aap-health.vault.yml` (made with `ansible-vault create`) holds `aap_username` and `aap_password`, for the API checks.
- The tags are `os`, `containers`, `logs`, `db`, `api`, `receptor` and `certs`.

**Settings you may set:**

- `aap_health_focus_inventories: [8]`: inventories always reported.
- `aap_url`: when the gateway refuses `https://localhost`.
- `aap_health_inventory_file`: when the installer inventory is not `inventory-growth` in the AAP account's home.
- `aap_health_log_since: 2h`.
- `aap_health_email: false`: no email (by default it is emailed to `report_email_to`, like the other reports).
- `aap_health_web: false`: keeps it off the report website (it is a page there like the other reports when `report_web_dest` is set).

Every setting is in [docs/VARIABLES_REFERENCE.md](docs/VARIABLES_REFERENCE.md#aap_health).

**As an AAP job template:**

- the playbook `playbooks/aap_health_check.yml`;
- the inventory with your AAP host (the group `aap_hosts`, or `target` / a Limit);
- a Machine credential that logs in as `ansible`, with sudo;
- a Red Hat Ansible Automation Platform credential (or the vaulted `aap_username` / `aap_password`).

The copy-ready Variables are in `aap/extra_vars/aap_health_check.yml`.

## Documentation

The guides, in the order a new site needs them: how it works and the setup first, then one guide per area.

| Doc | For |
|---|---|
| [docs/START_HERE.md](docs/START_HERE.md) | what the pieces are (Ansible and AAP in plain words) and how a run works |
| [docs/HOW_IT_FITS_TOGETHER.md](docs/HOW_IT_FITS_TOGETHER.md) | how code, inventory, variables, credentials and surveys fit; a setting's way from YAML to a host; updating the repository at work safely |
| [docs/SETUP_AAP.md](docs/SETUP_AAP.md) | the setup, step by step: Git, credentials, project, inventory, templates, ServiceNow |
| [docs/JOB_TEMPLATES.md](docs/JOB_TEMPLATES.md) | every job template, one line each: what kind of job it is, where its how-to is, and what one job hands to the next in a workflow |
| [docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md) | the ready-made workflows *AAP - set up job templates* makes (1.107.0: all of them), how to build one by hand, and over 30 ready-made workflows and schedules for Linux, Windows, VMware and Horizon (health, compliance, patching with a dry run first, ACT fixes, certificates, MariaDB, investigations, STIG Manager); schedules |
| [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md) | your inventory is already in AAP: the groups to add and which variables go on which group |
| [docs/IDM_INVENTORY.md](docs/IDM_INVENTORY.md) | the Linux inventory filled from IdM and kept in sync by AAP: your host groups mapped to the runbooks' groups |
| [docs/EXECUTION_ENVIRONMENT.md](docs/EXECUTION_ENVIRONMENT.md) | three execution environments, step by step, each built on your base EE (`base-ee:1.0`): Windows, AD and SQL (`ee/windows/`), Network - Cisco and Palo Alto (`ee/network/`), and the all-in-one for every job (`ee/all-in-one/`); build, check (`ee-check`), add to AAP; AD as an inventory (also a PDF) |
| [docs/VARIABLES.md](docs/VARIABLES.md) | settings (variables): what you can set, where to put it (settings file, AAP Variables box, extra variables), and which place wins; with [docs/VARIABLES_REFERENCE.md](docs/VARIABLES_REFERENCE.md), every setting and its default (generated) |
| [docs/SECRETS.md](docs/SECRETS.md) | where every secret lives |
| [docs/DRY_RUNS.md](docs/DRY_RUNS.md) | dry runs (Job type Check): what each template does in one, where to set it, examples |
| [docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md) | emailed reports: which jobs email, setting it up, green or red when a report finds problems, the look, adding email to a playbook |
| [docs/WEB_REPORTS.md](docs/WEB_REPORTS.md) | the report website: every report also as a web page (on a Windows server over WinRM, a folder or an upload), the file names, the JSON, nginx, the address in the job output |
| [docs/REPORT_SHARE.md](docs/REPORT_SHARE.md) | the reports on a Windows file share over SMB: set up, test and use, nothing mounted on the execution nodes |
| [docs/RUNBOOKS.md](docs/RUNBOOKS.md) | every check and finding, and what to do about it |
| [docs/LINUX_SW_INVENTORY.md](docs/LINUX_SW_INVENTORY.md) | the Linux software inventory: what is read, the three exports (shield files, CSV, JSON), the shield file's format, the share, the email, the SA-SWI codes |
| [docs/DISK_SPACE.md](docs/DISK_SPACE.md) | the disk space report (filesystems and LVM, 85% and 90%) and the tmp cleanup after an approval: what is read, what is deleted and what never is |
| [docs/STIGMAN_HEALTH.md](docs/STIGMAN_HEALTH.md) | STIG Manager's health (the address, each node, Keycloak, the database - through the API and on the hosts) and its availability every few minutes |
| [docs/AVAILABILITY.md](docs/AVAILABILITY.md) | the availability checks (STIG Manager, ServiceNow): what is asked, up / degraded / down, the availability over a day, a week and a month, the settings |
| [docs/MARIADB.md](docs/MARIADB.md) | the MariaDB and MySQL database checks, step by step: host installs and podman containers (rootless too), the monitor account per engine, verifying it, what was tested |
| [docs/AAP_HEALTH_CHECK.md](docs/AAP_HEALTH_CHECK.md) | the read-only AAP health check: what it checks, why an inventory's Hosts tab can return 503, running it on the AAP host or as a job template, settings, what to do when something is wrong |
| [docs/LOG_ANALYSIS.md](docs/LOG_ANALYSIS.md) | log analysis: a group of servers' (or vCenter's) warning and error lines grouped into patterns, ACT's likely cause and fix for the top ones, the profiles |
| [docs/STIG_JOBS.md](docs/STIG_JOBS.md) | STIG jobs: which one to use - the full scan, the drift scan (both with an approval) and the answer file generator; where a new asset goes; the old templates to delete |
| [docs/STIG_DRIFT.md](docs/STIG_DRIFT.md) | STIG drift, Linux (OpenSCAP) and Windows (Evaluate-STIG): the scan against the host's checklist in STIG Manager and its earlier scans, the recheck of the open rules, and updating STIG Manager with an approval |
| [docs/CKL_IMPORT.md](docs/CKL_IMPORT.md) | checklists (CKL, CKLB - VMware's InSpec ones too) written into STIG Manager after an approval, or on a schedule as STIG Manager Watcher |
| [docs/EVALUATE_STIG_UPDATE.md](docs/EVALUATE_STIG_UPDATE.md) | Evaluate-STIG on the RHEL hosts updated from the zip in your own repository: what it checks, the answer files carried over, the quick run and the way back, setting it up |
| [docs/TRUST_STORES.md](docs/TRUST_STORES.md) | who still trusts a compromised or dropped CA: every trust store on Linux, Windows, vCenter and ESXi, the list that ships, the report and the removal |
| [docs/CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md) | the certificate renewals without incidents: each CSR ordered from your PKI's catalog item - finding the item's questions, the settings, the catalog test, what a run does |
| [docs/CERT_INSTALL.md](docs/CERT_INSTALL.md) | the issued certificate installed from its request, Linux and Windows: the check, the approval, the install and its rollback, what stays for a person, the restore; the CA bundle when the PKI sends only the .cer, and the job that keeps it current |
| [docs/CERT_INVENTORY.md](docs/CERT_INVENTORY.md) | every certificate the reports found, with the same fields, as CSV / JSON or a SQL Server table for a dashboard |
| [docs/NETWORK_CERTS.md](docs/NETWORK_CERTS.md) | the certificates of switches, firewalls, printers, iLO / iDRAC ..., read with a TLS hello and no login; the renewal job |
| [docs/NETWORK_CONFIG.md](docs/NETWORK_CONFIG.md) | Cisco IOS / IOS-XE and PAN-OS configurations backed up to git or a share, and loaded after an approved change request |
| [docs/NETWORK_PERF.md](docs/NETWORK_PERF.md) | the network performance report: latency, jitter and loss from each server to what it depends on, OCSP, interface errors, iperf3 pairs, each against its usual |
| [docs/MAIL_RELAY.md](docs/MAIL_RELAY.md) | the mail relay whitelist: the format job (one address per line, named), the add and remove workflows with their surveys, starting them from Orchestrator, what is refused |
| [docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md) | ServiceNow, step by step: the API account to ask for, the instance URL and API calls, firewall / proxy / DoD CA, the credential, a test ticket, and how to verify it in ServiceNow |
| [docs/SERVICENOW_WAR.md](docs/SERVICENOW_WAR.md) | the weekly activity report from ServiceNow: what the report has, what counts as completed, the account's rights, the template and its schedule, the week, the settings, ACT's narrative |
| [docs/SERVICENOW_INSIGHTS.md](docs/SERVICENOW_INSIGHTS.md) | the ticket insights: what the page has, how ACT is asked and what never reaches it, the account's rights, the template and its schedule, the window |
| [docs/REPORT_DATABASE.md](docs/REPORT_DATABASE.md) | the report database (MySQL 8.4 on the STIG Manager host): the ticket insights' figures month by month, the Trends on the pages, the backfill, what is never stored |
| [docs/WINDOWS.md](docs/WINDOWS.md) | Windows servers: WinRM, credential, inventory, templates, settings, patching, ACT |
| [docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md) | the Active Directory health report: the account it needs, the template, every section (and where "when", "which DC" and "by whom" come from), settings and troubleshooting; the account lockout job for the service desk (the audit policy, the survey, the unlock) |
| [docs/VMWARE.md](docs/VMWARE.md) | the VMware jobs: the collection the execution environment needs, the vCenter account and credential, one template per job with its survey |
| [docs/VM_DEPLOY_GUIDE.md](docs/VM_DEPLOY_GUIDE.md) | the VM deploy from a ServiceNow ticket, complete: setting it up in AAP step by step, how a run works, what requesters write, where the VM goes, configurations to copy, every message with what to do, every setting (also a PDF) |
| [docs/VM_ASK.md](docs/VM_ASK.md) | *VM - ask a question*: the question in plain words, how GenAI turns it into a query the job checks against a whitelist, what is refused, examples |
| [docs/VMWARE_ASK.md](docs/VMWARE_ASK.md) | VMware ask, the web page of *VM - ask a question*: what it reads and sends, behind STIG Manager's nginx (its network, the lines to add for /ask/, the card's certificate), the template, who asked what, settings, what to do when something is wrong |
| [docs/SITE_PORTAL.md](docs/SITE_PORTAL.md) | The site portal: the report website, the actions (the actions file and its rules), the live job view, the approvals, the portal looks, the Actions page's Ops center strip, and the ops-center wall (its sources, thresholds and feedback); behind STIG Manager's nginx or with its own; the AAP account, the template, the log, security, settings, what to do when something is wrong |
| [docs/AAP_KEYCLOAK.md](docs/AAP_KEYCLOAK.md) | AAP (2.5 or later, containerized) behind STIG Manager's nginx with the Keycloak (CAC) login, a checklist: check first, the nginx blocks to paste (`tools/aap-nginx-blocks.py` writes them to `./out`: AAP, the card's way to Keycloak, the report website), the gateway's proxy settings, a Keycloak realm of its own for AAP (`tools/aap-keycloak-realm.py`: a dry run by default, `--apply` makes it, no other realm touched), the authentication method and maps, tests with a table of failures, the way back; `tools/aap-keycloak-check.sh` prints PASS or FAIL and only reads |
| [docs/VM_OUTAGE_WATCH.md](docs/VM_OUTAGE_WATCH.md) | the VM outage watch: what it asks vCenter every few minutes, what counts as down or hung, the ticket per problem, what a later phase would restart; the outage review (one time line of vCenter's events, the tickets and ACT's notes, for the AAR) |
| [docs/HORIZON.md](docs/HORIZON.md) | the Horizon 8 (VDI) jobs: the service account and its Horizon role, the credential, the templates and surveys, the broken desktops and image push workflows step by step, what is not yet tried on a real Horizon |
| [docs/NETAPP.md](docs/NETAPP.md) | the NetApp ONTAP health report: the read-only ONTAP account, the credential, the template, every section, settings and troubleshooting |
| [docs/UCS.md](docs/UCS.md) | the Cisco UCS inventory: the read-only UCS Manager account, the credential, the template, every section and sheet, settings and troubleshooting |
| [docs/TENABLE.md](docs/TENABLE.md) | the Tenable.sc (ACAS) scan job: the account and its API keys, the credential, the template, how each host's scan zone is chosen, the report |
| [docs/INCIDENT_DOCS.md](docs/INCIDENT_DOCS.md) | the outage AAR and SOP Word documents: the two templates and the survey job, what the documents hold, ACT's tidy and suggestions, who gets them, settings and troubleshooting |
| [docs/APPROVED_COMMANDS.md](docs/APPROVED_COMMANDS.md) | how to pre-approve the commands ACT may run by itself, what can never be approved, and how to verify it |
| [docs/ADDING_ACT.md](docs/ADDING_ACT.md) | adding ACT later (explain / diagnose / self-heal), model tiers (a worker model for the bulk, 1.137.0), and writing your own check |
| [docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md) | the service-watch demo |
| [guides/](guides/) | **PDFs** of the guides (built by [docs/pdf/](docs/pdf/README.md)): the runbooks setup guide, the VM deploy guide, the service-watch demo, the ACT guides and leadership briefs |

## Layout

| Path | What |
|---|---|
| `playbooks/` | one playbook per job template |
| `playbooks/group_vars/` | **your settings**, one file per AAP group (`all.yml` = every host); yours - updates never overwrite them |
| `inventories/example/` | placeholder inventory and settings: copy to `inventories/site/` (yours, never in the public repository) |
| `roles/site_findings/`, `roles/site_email/`, `roles/site_act/` | what every job shares: the findings contract (start, run a check safely, report, publish, pass / fail), the emailed report and the report website, the bridge from a check's findings to ACT |
| `roles/check_*/` | one role per Linux health check; settings in `defaults/main.yml` |
| `roles/win_check_*/`, `roles/win_troubleshoot/`, `roles/win_patch/`, `roles/win_act/` | the Windows checks (a PowerShell script each, in `files/`), troubleshooting, patching and the ACT install |
| `roles/troubleshoot/`, `roles/patch/`, `roles/tmp_cleanup/`, `roles/log_analysis/`, `roles/fapolicyd*/`, `roles/podman_discover/` | the other Linux runbooks |
| `roles/check_stig/`, `roles/win_check_stig/`, `roles/stigman_update/`, `roles/stigman_ckl_import/`, `roles/evaluate_stig_update/`, `roles/poam/` | compliance: the drift reports and rechecks, the STIG Manager writes, Evaluate-STIG, POA&M |
| `roles/stigman_stack/`, `roles/vmware_ask_stack/` | the two deployments on podman / Quadlet: STIG Manager, and the VMware ask web page behind its nginx |
| `roles/check_certs/`, `roles/check_trust/`, `roles/cert_inventory/`, `roles/pki_bundle/`, `roles/net_certs/` | certificates and trust stores, the inventory for a dashboard, the DoD CA bundle, the network devices' certificates |
| `roles/net_config/`, `roles/net_perf/`, `roles/mail_relay/` | network device configurations, the network performance report, the mail relay whitelist |
| `roles/servicenow/`, `roles/servicenow_war/`, `roles/servicenow_insights/` | the tickets, the weekly activity report, the ticket insights |
| `roles/vmware_vm/` | every VMware job (vCenter's API through the certified `vmware.vmware` collection, from the execution environment) |
| `roles/horizon/`, `roles/ad_report/`, `roles/ad_lockout/`, `roles/ontap_report/`, `roles/ucs_report/`, `roles/tenable_scan/`, `roles/netmap_report/`, `roles/solarwinds_snapshot/` | Horizon, Active Directory, NetApp ONTAP, Cisco UCS, Tenable.sc, the SolarWinds network map and snapshot - each with its API reader and its report filter |
| `roles/service_watch/`, `roles/incident_docs/` | service watch; the AAR and SOP Word documents |
| `roles/aap_health/`, `roles/aap_email_question/`, `roles/aap_ee/`, `roles/aap_template_audit/` | AAP itself: the health check, the survey question, the execution environment setter, the template variables audit |
| `plugins/filter/` | the report builders and small filters (CSV reading, dates) - standard library only |
| `aap/credential_types/`, `aap/extra_vars/` | the custom credential types to create in AAP (input + injector YAML); the copy-ready Variables of every template |
| `ee/` | the execution environments: Windows / AD / SQL, Network, and the all-in-one, each built on your base EE |
| `poam/` | the POA&M list (CSV) for `poam_status.yml` |
| `vendor/act/`, `vendor/act-windows/` | ACT-Linux (the `act` tool and its roles) and ACT-Windows (`act.ps1`), vendored from a release |
| `scripts/update-from-release.sh`, `.ps1` | update YOUR copy from a new release - preview first; your own files (`poam/poam.csv`, `.site-local` paths, git-ignored files) are never touched |
| `scripts/update-act.sh`, `scripts/update-act-windows.sh`, `.ps1` | refresh `vendor/act` and `vendor/act-windows` from a newer release |
| `scripts/gen_variable_reference.py`, `scripts/gen_template_vars.py` | rewrite `docs/VARIABLES_REFERENCE.md` from the roles' defaults, and `aap/extra_vars/` from what each playbook reads (the tests check both are current) |
| `tests/` | the unit tests and the end-to-end tests (containers, a simulated vCenter, mocked APIs), the public-repository guard `check_public.py` |
| `guides/` | the PDFs of the guides |
| `docs/pdf/` | the scripts that build them into `guides/` (`docs/pdf/README.md`) |

## Leave hosts out of every job

List the hosts no job may touch in `all.yml`. Every job then leaves them out, before it connects to anything.

```yaml
site_exclude_hosts: [web02, "*.lab.example.mil"]   # whole or short names, either way; * and ?; any case
site_exclude_groups: [retired]                      # every host in these inventory groups
```

- **Every playbook that runs on hosts** (Linux, Windows, network, the reports) starts with the play *Leave hosts out
  (site_exclude_hosts)*. It connects to nothing. A host left out is never in the job's plays: no facts are gathered from
  it, and no task runs on it. A Limit or a target that names it does not bring it back.
- **Each report says so** in one line at its end: `Left out by site_exclude_hosts: 3 - old01..., web02...` (the first
  10 names). A host left out is never a finding, never *not reached*.
- **The VMware and Horizon jobs** keep their own lists (`vmware_protected_vms`, `vm_snapshot_cleanup_exclude_vms`,
  `esxi_security_exclude_hosts` ...). They also leave out the VMs, ESXi hosts and desktops named in
  `site_exclude_hosts`.
- `web01` leaves out `web01.example.mil`, and `web01.example.mil` leaves out a host called `web01`. An address, and a
  pattern with `*` or `?`, are matched as they are.

## Using it in AAP

Follow [docs/SETUP_AAP.md](docs/SETUP_AAP.md). In short: put this repository in your work Git
with your inventory in `inventories/site/`; create the credential types in `aap/credential_types/`
and the credentials; a project on the repository; an inventory sourced from
`inventories/site/hosts.yml`; one job template per playbook; then the workflows and schedules in
[docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md). Your inventory is already in
AAP? Keep it, and follow [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md)
instead of the inventory steps. Your RHEL hosts are enrolled in IdM? Let AAP fill the Linux inventory from it:
[docs/IDM_INVENTORY.md](docs/IDM_INVENTORY.md).

## The STIG Manager deployment

Job template **STIG Manager - deploy**: playbook `playbooks/stigman_deploy.yml`; credentials:
Machine, *STIG Manager database*, *TLS certificate* (and *Container registry login (hosts)* if
you pull with an account). It runs on the group `stigman` (or set `target`).

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
