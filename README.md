# Site automation

Runbooks for a site's servers and infrastructure, run from **Ansible Automation Platform 2.5+** (or the Ansible
CLI). What started as Linux health checks now covers **Linux and Windows servers, VMware (vCenter and ESXi),
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
  table is long; ServiceNow incidents opened, updated and resolved for the findings when you turn that on.

**New to Ansible or AAP? Start with [docs/START_HERE.md](docs/START_HERE.md)**, then follow
[docs/SETUP_AAP.md](docs/SETUP_AAP.md) step by step. The same guide as one printable PDF:
[docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf](docs/pdf/Site-Automation-Ops-Runbooks-Guide.pdf).

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
| Health check | `playbooks/health_check.yml` | read-only checks, picked per run: disk (with days-until-full), mounts, services, performance, time, network, logging, SELinux, fapolicyd, auditd, accounts, certificates, trust stores (`trust`: a compromised or dropped CA still trusted), patching, MariaDB / MySQL (`mariadb`, `mysql` or `database`), podman containers (`containers`: root's and every user's, found by themselves). Findings in plain words, each with a command to look further; fails hosts with findings so a workflow can react |
| Linux - disk space report / tmp cleanup | `playbooks/disk_report.yml`, `tmp_cleanup.yml` | every mounted filesystem and LVM thin pool or snapshot, 85% amber and 90% red, the volume groups' free space and the old tmp files on a full filesystem - a report that stays green; then, after an approval (workflow 42), the old files of `/tmp` and `/var/tmp` deleted on those hosts, never sockets or systemd's own ([docs/DISK_SPACE.md](docs/DISK_SPACE.md)) |
| **Database health - MariaDB / MySQL** | `playbooks/database_health.yml` | **the database check on its own, for MariaDB and MySQL (5.7, 8.0, 8.4), installed on the host or in podman containers** (one, several, found automatically, rootless): running, connections, long queries, replication, Galera and Group Replication, log errors, disk. Read-only; fails hosts with findings ([docs/MARIADB.md](docs/MARIADB.md)) |
| Troubleshoot | `playbooks/troubleshoot.yml` | on demand: the first commands an admin runs for a kind of problem (disk, performance, service, network, SELinux, fapolicyd, login, time, logs, MariaDB / MySQL), each explained, plus the matching checks |
| **Log analysis** | `playbooks/log_analysis.yml`, `vm_log_analysis.yml` | point it at a group of servers (Linux, Windows, a vCenter appliance), or at vCenter and its ESXi hosts through the API (no SSH): their warning and error log lines grouped into patterns across hosts, and ACT's likely cause, fix and confidence for the top ones; profiles switch on a product's logs with one word (`mid_server`, `iis`, `sql_server`, `nginx`, `vcsa` ...); a report, emailed ([docs/LOG_ANALYSIS.md](docs/LOG_ANALYSIS.md)) |
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
| STIG Manager - import checklists | `playbooks/stigman_ckl_import.yml` | workflow 40: checklists (CKL, CKLB) from a share - VMware's InSpec ones too, matched to the installed STIG by their rules - written to STIG Manager after an approval, a missing asset created with its STIGs; Accepted reviews left; or on a schedule as STIG Manager Watcher - written at once to whichever collection has the host (a staging collection for the new), the files done with moved to `imported/` ([docs/CKL_IMPORT.md](docs/CKL_IMPORT.md)) |
| Evaluate-STIG - update | `playbooks/evaluate_stig_update.yml` | Evaluate-STIG on the RHEL hosts updated from the zip you keep in your own repository - only where it is newer; your answer files carried over, the old folder kept, a quick run that puts the old one back if the new one does not work; Check lists each host's version ([docs/EVALUATE_STIG_UPDATE.md](docs/EVALUATE_STIG_UPDATE.md)) |
| POA&M status | `playbooks/poam_status.yml` | overdue / due-soon POA&M items from `poam/poam.csv`; optional STIG Manager cross-check for open CAT I/II findings with no POA&M item |
| STIG Manager - deploy | `playbooks/stigman_deploy.yml` | MySQL 8.4 + STIG Manager + nginx (TLS) on podman/Quadlet |

### Certificates and trust stores

| Job template | Playbook | What |
|---|---|---|
| Certificate report | `playbooks/cert_report.yml` | every certificate on every host (files, keystores, TLS endpoints) in one table, soonest expiry first; Satellite left out (`cert_skip_hosts`, `cert_skip_groups`) |
| Trust store report | `playbooks/trust_report.yml` | which trust stores still hold a compromised or dropped CA - the system bundles, p11-kit, Java cacerts, Python certifi, Node, podman containers, an application's files - and the Go programs, Node and NSS below the version that fixed it; the eMudhra (emSign) list of October 2026 ships with it, yours in `check_trust_extra`; read-only, or with `check_trust_remove: true` it takes them out (blocklist, keystore, bundles - backups first, Check mode shows the plan) ([docs/TRUST_STORES.md](docs/TRUST_STORES.md)) |
| VMware - trust store report | `playbooks/vmware_trust_report.yml` | vCenter's trusted root store and each ESXi host's trusted CAs against the same list, on its own (a vCenter credential, no inventory); `check_trust_remove: true` deletes them, Check mode first ([docs/TRUST_STORES.md](docs/TRUST_STORES.md)) |
| Windows - trust store report | `playbooks/win_trust_report.yml` | the Windows servers' certificate stores (Trusted Root, Intermediate CAs, AuthRoot, Trusted Publishers), Java cacerts and Python certifi against the same list; `check_trust_remove: true` puts them in Disallowed and deletes them, Check mode first; the check `trust` is in the Windows health check's weekly set ([docs/TRUST_STORES.md](docs/TRUST_STORES.md#windows)) |
| Certificate renewal - CSRs | `playbooks/cert_renew_csr.yml` | for each certificate file that expires: a new key and a CSR (same subject and names) on the host, the CSR attached to the certificate's ServiceNow incident; never Satellite's or subscription-manager's certificates |
| Windows certificate renewal - CSRs | `playbooks/win_cert_renew_csr.yml` | the same on Windows servers: a new key and CSR with `certreq`, on the certificate's ServiceNow ticket; the key stays on the server |
| Certificate install - check / approved | `playbooks/cert_install_check.yml`, `cert_install_approved.yml` | the certificate the PKI put on the catalog request, in place of the old one - after an approval: the files in their old layout, the service reloaded, the port checked; anything wrong, the old files go back ([docs/CERT_INSTALL.md](docs/CERT_INSTALL.md)) |
| Windows certificate install - check / approved | `playbooks/win_cert_install_check.yml`, `win_cert_install_approved.yml` | the same on Windows: `certreq -accept`, then the old certificate's bindings (https in HTTP.sys - IIS ..., Remote Desktop, WinRM) moved to it and checked; anything wrong, back on the old one |
| VMware - certificate request | `playbooks/vmware_cert_request.yml` | on demand, for the vCenters and ESXi hosts you name: a CSR made where the key stays, ordered from the PKI's catalog item now; prints the request (RITM) for the install ([docs/VMWARE.md](docs/VMWARE.md#request-a-certificate-now)) |
| VMware - certificate install - check / approved | `playbooks/vmware_cert_install_check.yml`, `vmware_cert_install_approved.yml` | the certificate the PKI put on the catalog request of a CSR of vCenter or an ESXi host, installed where its key waits - after an approval: the hosts in use (the CA chain in vCenter's trusted roots, vCenter must show it), then vCenter's Machine SSL certificate ([docs/VMWARE.md](docs/VMWARE.md#installing-the-issued-certificates-of-vcenter-and-the-esxi-hosts-with-an-approval)) |
| Certificate restore / Windows certificate restore | `playbooks/cert_restore.yml`, `win_cert_restore.yml` | an installed certificate's old one back, from the copy the install made |
| PKI - DoD bundle check | `playbooks/pki_bundle_check.yml` | weekly: the DoD PKI's CA bundle downloaded, checked (DISA's signature, every CA up to your roots) and compared with your copy (`cert_install_ca_bundle` - the bundle the certificate installs complete a chain from when the PKI sends only the .cer); a newer one emailed, the checked .p7b attached ([docs/CERT_INSTALL.md](docs/CERT_INSTALL.md#keeping-the-ca-bundle-current)) |
| Network device certificate report / renewal | `playbooks/net_cert_report.yml`, `net_cert_renew.yml` | the certificates of switches, firewalls, printers, iLO / iDRAC ... read from the controller (an inventory group, or subnets swept) - no login to the devices; the renewal opens an incident per certificate due, and can make the CSR on Cisco IOS / IOS-XE ([docs/NETWORK_CERTS.md](docs/NETWORK_CERTS.md)) |
| (every certificate report) | `cert_inventory_files`, `cert_inventory_sql` | every certificate with the same fields - issuer, names, key, fingerprints, tier (root, intermediate, CA-signed, self-signed) - as CSV / JSON files or a SQL Server table, for a dashboard such as Power BI ([docs/CERT_INVENTORY.md](docs/CERT_INVENTORY.md)) |

### Network devices and the network

| Job template | Playbook | What |
|---|---|---|
| Network config backup / load | `playbooks/net_config_backup.yml`, `net_config_load.yml` | Cisco IOS / IOS-XE and Palo Alto PAN-OS: each running configuration kept in a private git repository (a commit when one changed) or on a file share, the diff in the report and optionally an incident; the load sends lines or restores a backup, after an approved change request, tried in Check first ([docs/NETWORK_CONFIG.md](docs/NETWORK_CONFIG.md)) |
| Network performance report | `playbooks/net_perf_report.yml` | from each Linux host: latency, jitter and loss to the targets it depends on (ping, timed TCP connects), its OCSP responders answering a real request, its interfaces' drops and errors, each path against the host's own usual (kept on the host); iperf3 throughput between named pairs ([docs/NETWORK_PERF.md](docs/NETWORK_PERF.md)) |
| Mail relay whitelist | `playbooks/mail_relay_format.yml`, `mail_relay_plan.yml`, `mail_relay_approved.yml`, `mail_relay_unused.yml` | Postfix's mynetworks in main.cf: one address per line, each named from DNS and its VM's vCenter folder (a copy kept, Check first); add IP addresses or subnets with a required comment, or remove them, after an approval in AAP - the plan emails the exact lines, the apply step checks the configuration, reads the list back as Postfix reads it and reloads Postfix; a third workflow removes the addresses that sent no mail in six months, after an approval ([docs/MAIL_RELAY.md](docs/MAIL_RELAY.md)) |

### ServiceNow

| Job template | Playbook | What |
|---|---|---|
| ServiceNow tickets | `playbooks/servicenow_tickets.yml` | workflow step: one incident per finding, updated (not duplicated) on later runs, noted or resolved when it clears |
| ServiceNow health | `playbooks/servicenow_health.yml` | the instance answers its API; the MID Servers up, validated, reporting, on one version, their queue moving, no open issues - and on their Windows hosts the MID service, its agent log (hung? SEVERE lines), JVM restarts, the drive and the instance URL, in one report ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md#mid-servers)) |
| ServiceNow - weekly activity report | `playbooks/servicenow_war.yml` | everything an assignment group had in the week - incidents, catalog tasks, changes, change tasks, problem tasks: completed (with the date of closure), opened, still open and for how long, who had what; one line per task ready to paste into the WAR, an Excel workbook on the email, the page on the website; ACT's narrative when asked ([docs/SERVICENOW_WAR.md](docs/SERVICENOW_WAR.md)) |
| ServiceNow - ticket insights | `playbooks/servicenow_insights.yml` | months of tickets for management, a web page for the site and one per team: the top incident issues and their fix (ACT groups them and writes the fixes, names replaced before anything reaches the model), the most requested services, keeping up, P1/P2s, problem candidates, tickets passed between groups, changes and what followed, bare close notes, each team's All-stars, knowledge article drafts ([docs/SERVICENOW_INSIGHTS.md](docs/SERVICENOW_INSIGHTS.md)) |
| ServiceNow - catalog test | `playbooks/servicenow_catalog_test.yml` | for the certificate renewals ordering the PKI's catalog item (`cert_renew_servicenow: catalog`, no incidents): lists the item's questions by their internal names, checks the settings, and can order it once with a test CSR ([docs/CERT_RENEWAL_CATALOG.md](docs/CERT_RENEWAL_CATALOG.md)) |
| ServiceNow - test ticket | `playbooks/servicenow_test_ticket.yml` | proves the setup: logs in, checks the assignment group, opens a test incident, reads it back, adds a work note, resolves it; PASS / WARN / FAIL per step with the likely cause ([docs/SERVICENOW_SETUP.md](docs/SERVICENOW_SETUP.md)) |

### Windows servers and Active Directory

| Job template | Playbook | What |
|---|---|---|
| **Windows** health check, troubleshoot, certificate report, connection test | `playbooks/win_*.yml` | the same for Windows servers over WinRM: disk, services, performance, time, network, event log, security, audit policy, accounts, certificates, patching ([docs/WINDOWS.md](docs/WINDOWS.md)) |
| **Active Directory** health report | `playbooks/ad_health_report.yml` | read-only, every domain controller over WinRM: replication (each DC's partners, and every DC's latest change reaching the PDC), dcdiag, FSMO roles, SYSVOL / DFSR, DNS SRV records, time, DC events, LDAPS / KDC certificates, backups, RID pool, krbtgt, trusts, sites - and **what changed** in the last day: privileged group members added / removed (when, on which DC, by whom), accounts created / deleted / disabled / unlocked, GPOs and their links, schema, sites, subnets, DCs - coloured, problems first, emailed ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md)) |
| **Active Directory** account lockout | `playbooks/ad_lockout.yml` | for the service desk: is an account locked out, and **which computer is locking it out** - every DC's own bad password count and its Security log (4740 and the caller computer, 4771 / 4768 Kerberos and the client's address, 4776 NTLM and the workstation, 4625), the sources ranked with what each one usually is (a phone, a service, a scheduled task, a mapped drive, a disconnected Remote Desktop session); empty = every account locked out now; optional unlock on the PDC emulator, and a look on the source computers of the inventory ([docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md#account-lockout)) |
| ACT for Windows - install | `playbooks/win_act_install.yml` | ACT on the Windows servers in `C:\ProgramData\act`: locked down to Administrators and SYSTEM, unblocked, SHA256-checked every run |

### VMware: vCenter and ESXi

| Job template | Playbook | What |
|---|---|---|
| **VMware (vCenter)**: restart, shut down, snapshot (no memory), delete snapshot, notes, change VLAN, secure boot report, datastore report, snapshot report and cleanup (with an approval workflow), health report (every datacenter, cluster and host: HA, DRS, vCLS, hosts, hardware, single sign-on, the hosts' and VMs' network drops and errors), alarms report (also the alarms that fired and cleared) and its ACT analysis (likely cause, fix, confidence), ESXi security settings every night (SSH and ESXi Shell off, timeouts, lockdown normal), vCenter and ESXi certificates (a report, and a renewal: an incident and a CSR for each one that expires soon), capacity planning overall and per cluster (runways, hosts recommended, math vs GenAI); the reports cover several vCenters in one (`vmware_vcenters`); a weekly report of the VMs created (by whom, from which ticket); deploy VMs from a template for a ServiceNow ticket (one or several per ticket) | `playbooks/vm_*.yml`, `playbooks/esxi_security.yml`, `playbooks/vmware_cert_report.yml`, `playbooks/vmware_cert_renew.yml` | jobs on virtual machines through vCenter; restart / shut down / VLAN change never touch the AAP server's VM; dry run as *Check* ([docs/VMWARE.md](docs/VMWARE.md)) |
| VMware inventories and the ESXi network check | `playbooks/vmware_version_report.yml`, `vmware_hardware_report.yml`, `vm_inventory_report.yml`, `vm_tools_report.yml`, `vm_rightsizing_report.yml`, `esxi_network_check.yml` | read-only, across the vCenters, with the read-only account: the version and build of each vCenter and ESXi host; the hosts' hardware and firmware; every VM by folder; VMware Tools, hardware version and guest OS; each powered-on VM's size against what it used (too big, about right, too small); and would vMotion work between these hosts at full speed - esxcli's ping through vCenter, MTU, storage latency, SSH stays off ([docs/VMWARE.md](docs/VMWARE.md)) |
| **VM outage watch** | `playbooks/vm_outage_watch.yml` | every 5 to 15 minutes: is something down or hung right now - hosts not responding, HA isolated or restarting VMs, datastores lost, all paths down, VMs on a question, hung VMs (no heartbeat AND no answer on the network, checked twice). Quiet when clean; a report and a ServiceNow incident per problem when not. Watch only: it restarts nothing yet, and lists what a later phase would restart ([docs/VM_OUTAGE_WATCH.md](docs/VM_OUTAGE_WATCH.md)) |
| **VM - ask a question** | `playbooks/vm_ask.yml` | a question about vCenter in plain words - *all VMs with dbv in the name*, *VMs not restarted in 90 days*, *drives under 10% free*, *VMs vMotioned today* - answered from every vCenter, joins of VMs, hosts, clusters, datastores, snapshots and alarms included; GenAI only turns it into a query the job checks against a whitelist; **read-only**: a question that asks for a change is refused ([VM_ASK.md](docs/VM_ASK.md)) |
| **VMware ask** (the web page) | `playbooks/vmware_ask_deploy.yml` | the same as a web page behind STIG Manager's nginx at /ask/ (its mTLS - the card - says who asks), type the question, the answer in seconds from vCenter read every 10 minutes - the job's guard, GenAI prompt, whitelist and readers, a read-only vCenter account; who asked what kept (the journal, SQLite); the service in your execution environment image on podman/Quadlet, on nginx's network, the lines for nginx written for you to add ([VMWARE_ASK.md](docs/VMWARE_ASK.md)) |
| **VMware pick lists** | `playbooks/vm_pick_refresh.yml`, `vm_snapshot_delete_picked.yml`, `vm_reboot_picked.yml` | choose at launch which snapshots to delete or which VMs to restart (or only the hung ones), from a survey list a scheduled job keeps current; each pick checked again live, then plan, approval, exactly the plan ([docs/VMWARE.md](docs/VMWARE.md), "Pick lists") |

### Horizon 8 (VDI)

| Job template | Playbook | What |
|---|---|---|
| **Horizon 8 (VDI)**: API test, health report, capacity report, events report (failed logons, errors, admin changes), settings report (against a baseline), sessions report (old clients, stuck launches), logon times report, FSLogix profiles report (container sizes over SMB), internal VMs report (instant clones' left-over cp-* VMs), golden image check (READY / NOT READY before a push), message users, ESXi host maintenance (plan -> approval -> start, end), broken desktops (check -> approval -> fix), user help for the service desk, disconnected session cleanup, golden image push (plan -> approval -> push) | `playbooks/horizon_*.yml` | the Connection Server's REST API from the AAP side, any Horizon 8 release (each path's API version found by asking); instant clones recovered, sessions logged off, images pushed only after approval or when asked; dry run as *Check* ([docs/HORIZON.md](docs/HORIZON.md)) |

### Storage, hardware and vulnerability scanning

| Job template | Playbook | What |
|---|---|---|
| **NetApp ONTAP** health report | `playbooks/ontap_health_report.yml`, `ontap_hardware_report.yml` | read-only (REST GET): clusters, nodes, health alerts, events, hardware, disks, network and CRC errors, aggregates, volumes, inodes, snapshots, SnapMirror, peers, SVMs, CIFS, LUNs, certificates, time - coloured, problems first, emailed; and the hardware inventory of every cluster on its own, like the VMware one ([docs/NETAPP.md](docs/NETAPP.md)) |
| **Cisco UCS** inventory | `playbooks/ucs_inventory_report.yml` | read-only (UCS Manager's XML API, HTTPS): every domain's fabric interconnects, chassis and IO modules, blades and rack servers with their serial numbers, service profiles, CPUs, memory, BIOS, CIMC, adapter and storage controller firmware, power supplies, every running firmware and the critical and major faults - emailed with an Excel workbook |
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
| AAP - report email question | `playbooks/aap_email_question.yml` | adds an optional **Also email the report to** box to every report template's survey (and their workflows) through AAP's API: whoever launches a report can send that run to more people, on top of the template's usual recipients ([docs/EMAIL_REPORTS.md](docs/EMAIL_REPORTS.md), "Also email the report to") |
| AAP - set execution environment | `playbooks/aap_ee.yml` | puts one execution environment on many job templates at once through AAP's API, after a preview; the AAP credential only ([docs/EXECUTION_ENVIRONMENT.md](docs/EXECUTION_ENVIRONMENT.md#step-3-use-it)) |
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
| [docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md) | how to build any workflow, and over 30 ready-made workflows and schedules for Linux, Windows, VMware and Horizon (health, compliance, patching with a dry run first, ACT fixes, certificates, MariaDB, investigations, STIG Manager); schedules |
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
| [docs/DISK_SPACE.md](docs/DISK_SPACE.md) | the disk space report (filesystems and LVM, 85% and 90%) and the tmp cleanup after an approval: what is read, what is deleted and what never is |
| [docs/MARIADB.md](docs/MARIADB.md) | the MariaDB and MySQL database checks, step by step: host installs and podman containers (rootless too), the monitor account per engine, verifying it, what was tested |
| [docs/AAP_HEALTH_CHECK.md](docs/AAP_HEALTH_CHECK.md) | the read-only AAP health check: what it checks, why an inventory's Hosts tab can return 503, running it on the AAP host or as a job template, settings, what to do when something is wrong |
| [docs/LOG_ANALYSIS.md](docs/LOG_ANALYSIS.md) | log analysis: a group of servers' (or vCenter's) warning and error lines grouped into patterns, ACT's likely cause and fix for the top ones, the profiles |
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
| [docs/WINDOWS.md](docs/WINDOWS.md) | Windows servers: WinRM, credential, inventory, templates, settings, patching, ACT |
| [docs/ACTIVE_DIRECTORY.md](docs/ACTIVE_DIRECTORY.md) | the Active Directory health report: the account it needs, the template, every section (and where "when", "which DC" and "by whom" come from), settings and troubleshooting; the account lockout job for the service desk (the audit policy, the survey, the unlock) |
| [docs/VMWARE.md](docs/VMWARE.md) | the VMware jobs: the collection the execution environment needs, the vCenter account and credential, one template per job with its survey |
| [docs/VM_DEPLOY_GUIDE.md](docs/VM_DEPLOY_GUIDE.md) | the VM deploy from a ServiceNow ticket, complete: setting it up in AAP step by step, how a run works, what requesters write, where the VM goes, configurations to copy, every message with what to do, every setting (also a PDF) |
| [docs/VM_ASK.md](docs/VM_ASK.md) | *VM - ask a question*: the question in plain words, how GenAI turns it into a query the job checks against a whitelist, what is refused, examples |
| [docs/VMWARE_ASK.md](docs/VMWARE_ASK.md) | VMware ask, the web page of *VM - ask a question*: what it reads and sends, behind STIG Manager's nginx (its network, the lines to add for /ask/, the card's certificate), the template, who asked what, settings, what to do when something is wrong |
| [docs/VM_OUTAGE_WATCH.md](docs/VM_OUTAGE_WATCH.md) | the VM outage watch: what it asks vCenter every few minutes, what counts as down or hung, the ticket per problem, what a later phase would restart |
| [docs/HORIZON.md](docs/HORIZON.md) | the Horizon 8 (VDI) jobs: the service account and its Horizon role, the credential, the templates and surveys, the broken desktops and image push workflows step by step, what is not yet tried on a real Horizon |
| [docs/NETAPP.md](docs/NETAPP.md) | the NetApp ONTAP health report: the read-only ONTAP account, the credential, the template, every section, settings and troubleshooting |
| [docs/UCS.md](docs/UCS.md) | the Cisco UCS inventory: the read-only UCS Manager account, the credential, the template, every section and sheet, settings and troubleshooting |
| [docs/TENABLE.md](docs/TENABLE.md) | the Tenable.sc (ACAS) scan job: the account and its API keys, the credential, the template, how each host's scan zone is chosen, the report |
| [docs/INCIDENT_DOCS.md](docs/INCIDENT_DOCS.md) | the outage AAR and SOP Word documents: the two templates and the survey job, what the documents hold, ACT's tidy and suggestions, who gets them, settings and troubleshooting |
| [docs/APPROVED_COMMANDS.md](docs/APPROVED_COMMANDS.md) | how to pre-approve the commands ACT may run by itself, what can never be approved, and how to verify it |
| [docs/ADDING_ACT.md](docs/ADDING_ACT.md) | adding ACT later (explain / diagnose / self-heal), and writing your own check |
| [docs/SERVICE_WATCH_DEMO.md](docs/SERVICE_WATCH_DEMO.md) | the service-watch demo |
| [docs/pdf/](docs/pdf/README.md) | **PDFs** of the guides: the runbooks setup guide, the VM deploy guide, the service-watch demo, the ACT guides and leadership briefs |

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
| `roles/horizon/`, `roles/ad_report/`, `roles/ad_lockout/`, `roles/ontap_report/`, `roles/ucs_report/`, `roles/tenable_scan/` | Horizon, Active Directory, NetApp ONTAP, Cisco UCS, Tenable.sc - each with its API reader and its report filter |
| `roles/service_watch/`, `roles/incident_docs/` | service watch; the AAR and SOP Word documents |
| `roles/aap_health/`, `roles/aap_email_question/`, `roles/aap_ee/` | AAP itself: the health check, the survey question, the execution environment setter |
| `plugins/filter/` | the report builders and small filters (CSV reading, dates) - standard library only |
| `aap/credential_types/`, `aap/extra_vars/` | the custom credential types to create in AAP (input + injector YAML); the copy-ready Variables of every template |
| `ee/` | the execution environments: Windows / AD / SQL, Network, and the all-in-one, each built on your base EE |
| `poam/` | the POA&M list (CSV) for `poam_status.yml` |
| `vendor/act/`, `vendor/act-windows/` | ACT-Linux (the `act` tool and its roles) and ACT-Windows (`act.ps1`), vendored from a release |
| `scripts/update-from-release.sh`, `.ps1` | update YOUR copy from a new release - preview first; your own files (`poam/poam.csv`, `.site-local` paths, git-ignored files) are never touched |
| `scripts/update-act.sh`, `scripts/update-act-windows.sh`, `.ps1` | refresh `vendor/act` and `vendor/act-windows` from a newer release |
| `scripts/gen_variable_reference.py`, `scripts/gen_template_vars.py` | rewrite `docs/VARIABLES_REFERENCE.md` from the roles' defaults, and `aap/extra_vars/` from what each playbook reads (the tests check both are current) |
| `tests/` | the unit tests and the end-to-end tests (containers, a simulated vCenter, mocked APIs), the public-repository guard `check_public.py` |
| `docs/pdf/` | the PDFs and the scripts that build them (`docs/pdf/README.md`) |

## Using it in AAP

Follow [docs/SETUP_AAP.md](docs/SETUP_AAP.md). In short: put this repository in your work Git
with your inventory in `inventories/site/`; create the credential types in `aap/credential_types/`
and the credentials; a project on the repository; an inventory sourced from
`inventories/site/hosts.yml`; one job template per playbook; then the workflows and schedules in
[docs/WORKFLOWS_AND_SCHEDULES.md](docs/WORKFLOWS_AND_SCHEDULES.md). Your inventory is already in
AAP? Keep it, and follow [docs/USING_YOUR_AAP_INVENTORY.md](docs/USING_YOUR_AAP_INVENTORY.md)
instead of the inventory steps. Your RHEL hosts are enrolled in IdM? Let AAP fill the Linux inventory from it:
[docs/IDM_INVENTORY.md](docs/IDM_INVENTORY.md).

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
